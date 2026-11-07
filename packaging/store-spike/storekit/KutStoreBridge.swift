// Pont StoreKit 2 (spike ADR-0013 étape 13). JETABLE. Interface C : kutstore.h.
import Foundation
import StoreKit

// MARK: - utilitaires

private let isoFormatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()

private func iso(_ d: Date?) -> Any { d.map { isoFormatter.string(from: $0) } ?? NSNull() }

private func json(_ object: Any) -> String {
    guard JSONSerialization.isValidJSONObject(object),
          let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else { return "{\"error\":\"json\"}" }
    return text
}

private func errorJSON(_ kind: String, _ error: Error?) -> String {
    // Le message est borné : jamais l'entrée entière, jamais de secret.
    let text = error.map { String(String(describing: $0).prefix(300)) } ?? ""
    return json(["error": kind, "detail": text])
}

/// Boîte partagée entre la tâche et l'appelant : survit à un délai dépassé (la tâche écrit dans la boîte, personne ne lit).
private final class Box: @unchecked Sendable {
    let sem = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var _status: Int32 = 6
    private var _text = "{\"error\":\"internal\"}"
    func set(_ status: Int32, _ text: String) {
        lock.lock(); _status = status; _text = text; lock.unlock(); sem.signal()
    }
    func get() -> (Int32, String) { lock.lock(); defer { lock.unlock() }; return (_status, _text) }
}

private func transactionDict(_ t: Transaction) -> [String: Any] {
    [
        "productID": t.productID,
        "productType": t.productType.rawValue,
        "originalPurchaseDate": iso(t.originalPurchaseDate),
        "purchaseDate": iso(t.purchaseDate),
        "expirationDate": iso(t.expirationDate),
        "revocationDate": iso(t.revocationDate),
        "signedDate": iso(t.signedDate),
        "isUpgraded": t.isUpgraded,
        "ownershipType": t.ownershipType.rawValue,
        "environment": t.environment.rawValue,
        "id": String(t.id),
        "originalID": String(t.originalID),
    ]
}

// MARK: - état global

private let stateLock = NSLock()
private var isShutdown = false
private var tasks: [Task<Void, Never>] = []
private var updatesStarted = false
private let eventQueue = NSCondition()
private var events: [String] = []

private func currentlyShutdown() -> Bool { stateLock.lock(); defer { stateLock.unlock() }; return isShutdown }

// MARK: - opérations

private func runOperation(_ op: Int32, _ arg: String) async -> (Int32, String) {
    do {
        switch op {
        case 1:
            var items: [[String: Any]] = []
            var rejected = 0
            for await result in Transaction.currentEntitlements {
                switch result {
                case .verified(let t): items.append(transactionDict(t))
                case .unverified: rejected += 1          // jamais accepté
                }
            }
            return (0, json(["entitlements": items, "rejected": rejected]))
        case 2:
            let result = try await AppTransaction.shared
            switch result {
            case .verified(let a):
                return (0, json([
                    "originalPurchaseDate": iso(a.originalPurchaseDate),
                    "signedDate": iso(a.signedDate),
                    "originalAppVersion": a.originalAppVersion,
                    "environment": a.environment.rawValue,
                    "appVersion": a.appVersion,
                    "verified": true,
                ]))
            case .unverified(_, let err):
                return (4, errorJSON("unverified", err))
            }
        case 3:
            try await AppStore.sync()
            return (0, json(["synced": true]))
        case 4:
            let ids = arg.split(separator: ",", omittingEmptySubsequences: true).map(String.init)
            if ids.isEmpty || ids.count > 32 || ids.contains(where: { $0.utf8.count > 200 }) {
                return (2, json(["error": "bad_argument"]))
            }
            let products = try await Product.products(for: ids)
            let list = products.map { p -> [String: Any] in
                ["id": p.id, "type": p.type.rawValue, "displayPrice": p.displayPrice, "displayName": p.displayName,
                 "isFamilyShareable": p.isFamilyShareable,
                 "subscriptionPeriod": p.subscription.map { "\($0.subscriptionPeriod.value) \($0.subscriptionPeriod.unit)" } ?? NSNull(),
                 "introOffer": p.subscription?.introductoryOffer.map { "\($0.paymentMode.rawValue) \($0.period.value) \($0.period.unit)" } ?? NSNull()]
            }
            return (0, json(["products": list]))
        case 5:
            startUpdates()
            return (0, json(["listening": true]))
        default:
            return (2, json(["error": "unknown_op"]))
        }
    } catch is CancellationError {
        return (5, json(["error": "cancelled"]))
    } catch {
        return (3, errorJSON("storekit", error))
    }
}

private func pushEvent(_ text: String) {
    eventQueue.lock(); events.append(text); eventQueue.broadcast(); eventQueue.unlock()
}

private func startUpdates() {
    stateLock.lock()
    if updatesStarted || isShutdown { stateLock.unlock(); return }
    updatesStarted = true
    let task = Task.detached {
        for await result in Transaction.updates {
            if Task.isCancelled { break }
            var payload: [String: Any]
            switch result {
            case .verified(let t):
                payload = ["verified": true, "transaction": transactionDict(t)]
                await t.finish()
            case .unverified(let t, let err):
                payload = ["verified": false, "productID": t.productID, "reason": String(String(describing: err).prefix(200))]
            }
            pushEvent(json(payload))
        }
    }
    tasks.append(task)
    stateLock.unlock()
}

private func dup(_ s: String) -> UnsafeMutablePointer<CChar>? { strdup(s) }

// MARK: - interface C

@_cdecl("kutstore_abi_version")
public func kutstore_abi_version() -> Int32 { 1 }

@_cdecl("kutstore_call")
public func kutstore_call(_ op: Int32, _ arg: UnsafePointer<CChar>?, _ timeoutMs: Int32,
                          _ out: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32 {
    guard let out else { return 2 }
    out.pointee = nil
    if currentlyShutdown() { out.pointee = dup("{\"error\":\"shutdown\"}"); return 5 }
    var argument = ""
    if let arg {
        // Bornée avant d'allouer : une ligne énorme ne fabrique pas de String énorme.
        let length = strnlen(arg, 8193)
        if length > 8192 { out.pointee = dup("{\"error\":\"bad_argument\"}"); return 2 }
        argument = String(cString: arg)
    }
    let timeout = Int(max(1, min(timeoutMs, 120_000)))
    let box = Box()
    let task = Task.detached {
        let (status, text) = await runOperation(op, argument)
        box.set(status, text)
    }
    stateLock.lock(); tasks.append(task); stateLock.unlock()
    if box.sem.wait(timeout: .now() + .milliseconds(timeout)) == .timedOut {
        task.cancel()
        out.pointee = dup("{\"error\":\"timeout\"}")
        return 1
    }
    let (status, text) = box.get()
    out.pointee = dup(text)
    return out.pointee == nil ? 6 : status
}

@_cdecl("kutstore_poll_update")
public func kutstore_poll_update(_ timeoutMs: Int32, _ out: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32 {
    guard let out else { return 2 }
    out.pointee = nil
    let deadline = Date(timeIntervalSinceNow: Double(max(1, min(timeoutMs, 120_000))) / 1000.0)
    eventQueue.lock()
    defer { eventQueue.unlock() }
    while events.isEmpty {
        if currentlyShutdown() { out.pointee = dup("{\"error\":\"shutdown\"}"); return 5 }
        if !eventQueue.wait(until: deadline) { out.pointee = dup("{\"error\":\"poll_timeout\"}"); return 7 }
    }
    out.pointee = dup(events.removeFirst())
    return 0
}

@_cdecl("kutstore_free")
public func kutstore_free(_ p: UnsafeMutablePointer<CChar>?) { free(p) }

@_cdecl("kutstore_shutdown")
public func kutstore_shutdown() {
    stateLock.lock()
    isShutdown = true
    let all = tasks
    tasks.removeAll()
    stateLock.unlock()
    all.forEach { $0.cancel() }
    eventQueue.lock(); eventQueue.broadcast(); eventQueue.unlock()
}
