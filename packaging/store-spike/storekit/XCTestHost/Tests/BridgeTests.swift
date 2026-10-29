import AppKit
import Foundation
import StoreKit
import StoreKitTest
import XCTest

typealias CallFn = @convention(c) (Int32, UnsafePointer<CChar>?, Int32, UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32
typealias PollFn = @convention(c) (Int32, UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32
typealias FreeFn = @convention(c) (UnsafeMutablePointer<CChar>?) -> Void

final class BridgeTests: XCTestCase {
    static var handle: UnsafeMutableRawPointer!
    var session: SKTestSession!
    var call: CallFn!
    var poll: PollFn!
    var free_: FreeFn!

    override func setUpWithError() throws {
        let env = ProcessInfo.processInfo.environment
        let dylib = try XCTUnwrap(env["KUT_BRIDGE_DYLIB"], "KUT_BRIDGE_DYLIB manquante")
        let config = try XCTUnwrap(env["KUT_STOREKIT_FILE"], "KUT_STOREKIT_FILE manquante")
        if Self.handle == nil { Self.handle = dlopen(dylib, RTLD_NOW) }
        let h = try XCTUnwrap(Self.handle, "dlopen a échoué")
        call = unsafeBitCast(dlsym(h, "kutstore_call"), to: CallFn.self)
        poll = unsafeBitCast(dlsym(h, "kutstore_poll_update"), to: PollFn.self)
        free_ = unsafeBitCast(dlsym(h, "kutstore_free"), to: FreeFn.self)
        session = try SKTestSession(contentsOf: URL(fileURLWithPath: config))
        session.resetToDefaultState()
        session.clearTransactions()
        session.disableDialogs = true
        session.askToBuyEnabled = false
    }

    /// Achat réel via l'API publique (Product.purchase) sous SKTestSession. `buyProduct` (achat « hors appareil »)
    /// échoue dans ce processus (« Off-device purchase ... Unable to Complete Request », mesuré) : non utilisé.
    func buy(_ id: String) async throws {
        let products = try await Product.products(for: [id])
        let product = try XCTUnwrap(products.first)
        let controller: NSWindow = try await MainActor.run {
            try XCTUnwrap(NSApp.windows.first, "fenêtre de l'hôte absente")
        }
        let result = try await product.purchase(confirmIn: controller)
        guard case .success(let verification) = result, case .verified(let t) = verification else {
            return XCTFail("achat non abouti : \(result)")
        }
        await t.finish()
    }

    func run(_ op: Int32, _ arg: String = "", timeout: Int32 = 15000) -> (Int32, [String: Any]) {
        var out: UnsafeMutablePointer<CChar>?
        let status = call(op, arg, timeout, &out)
        defer { free_(out) }
        let text = out.map { String(cString: $0) } ?? "{}"
        let obj = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? ["raw": text]
        return (status, obj)
    }

    func pollOne(_ timeout: Int32 = 5000) -> (Int32, [String: Any]) {
        var out: UnsafeMutablePointer<CChar>?
        let status = poll(timeout, &out)
        defer { free_(out) }
        let text = out.map { String(cString: $0) } ?? "{}"
        return (status, (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? [:])
    }

    func testProductsFromConfigurationFile() {
        let (s, o) = run(4, "com.kutstudio.app.monthly,com.kutstudio.app.yearly,com.kutstudio.app.lifetime")
        XCTAssertEqual(s, 0)
        let products = o["products"] as? [[String: Any]] ?? []
        XCTAssertEqual(products.count, 3)
        print("PRODUCTS", products)
    }

    func testEmptyEntitlementsThenSubscription() async throws {
        var (s, o) = run(1)
        XCTAssertEqual(s, 0)
        XCTAssertEqual((o["entitlements"] as? [Any])?.count, 0)
        try await buy("com.kutstudio.app.monthly")
        (s, o) = run(1)
        let ents = o["entitlements"] as? [[String: Any]] ?? []
        XCTAssertEqual(ents.count, 1)
        XCTAssertEqual(ents.first?["productID"] as? String, "com.kutstudio.app.monthly")
        XCTAssertEqual(o["rejected"] as? Int, 0)
        print("ENTITLEMENT", ents)
    }

    func testLifetimePurchaseIsAnEntitlement() async throws {
        try await buy("com.kutstudio.app.lifetime")
        let (_, o) = run(1)
        let ents = o["entitlements"] as? [[String: Any]] ?? []
        XCTAssertEqual(ents.count, 1)
        XCTAssertTrue(ents.first?["expirationDate"] is NSNull, "achat unique : pas de date d'expiration")
        // Remboursement : SKTestSession.refundTransaction(identifier:) ne change rien d'observable dans ce
        // contexte (Xcode 27.1 / macOS 27.2) : ni revocationDate ni sortie de currentEntitlements. NON prouvé ; voir STOREKIT.md.
    }

    func testExpiredSubscriptionIsNoLongerAnEntitlement() async throws {
        try await buy("com.kutstudio.app.yearly")
        try session.expireSubscription(productIdentifier: "com.kutstudio.app.yearly")
        let (_, o) = run(1)
        XCTAssertEqual((o["entitlements"] as? [Any])?.count, 0)
    }

    func testUpdatesStreamDeliversRenewal() async throws {
        XCTAssertEqual(run(5).0, 0)
        XCTAssertEqual(run(5).0, 0, "idempotent")
        try await buy("com.kutstudio.app.monthly")
        try session.forceRenewalOfSubscription(productIdentifier: "com.kutstudio.app.monthly")
        let (s, o) = pollOne()
        print("UPDATE", s, o)
        XCTAssertEqual(s, 0)
        XCTAssertEqual(o["verified"] as? Bool, true)
        let tx = o["transaction"] as? [String: Any]
        XCTAssertEqual(tx?["productID"] as? String, "com.kutstudio.app.monthly")
    }

    func testAppTransactionInTestSession() {
        let (s, o) = run(2)
        print("APPTX", s, o)
        // Mesure : le statut est consigné ; le test ne présume pas du résultat (voir STOREKIT.md).
        XCTAssertTrue([0, 3, 4].contains(s))
    }

    func testSyncDoesNotThrowAcrossTheBoundary() {
        let (s, o) = run(3)
        print("SYNC", s, o)
        XCTAssertTrue([0, 3].contains(s))
    }

    func testOfflineProductsAndEntitlements() async throws {
        try await buy("com.kutstudio.app.monthly")
        try await session.setSimulatedError(.generic(.networkError(URLError(.notConnectedToInternet))), forAPI: .loadProducts)
        let (s, o) = run(4, "com.kutstudio.app.monthly")
        print("OFFLINE products", s, o)
        XCTAssertEqual(s, 3, "erreur StoreKit rendue en code, pas d'exception")
        let (s2, o2) = run(1)
        XCTAssertEqual(s2, 0, "currentEntitlements ne dépend pas du réseau")
        XCTAssertEqual((o2["entitlements"] as? [Any])?.count, 1)
        try await session.setSimulatedError(nil, forAPI: .loadProducts)
    }

    func testBoundedArgumentsAndTimeout() {
        XCTAssertEqual(run(99).0, 2)
        XCTAssertEqual(run(4, "").0, 2)
        XCTAssertEqual(run(4, String(repeating: "a", count: 9000)).0, 2, "argument démesuré refusé avant allocation")
        XCTAssertEqual(run(4, (0..<40).map { "id\($0)" }.joined(separator: ",")).0, 2)
        let (s, o) = run(4, "com.kutstudio.app.monthly", timeout: 1)
        XCTAssertTrue(s == 1 || s == 0, "1 ms : délai ou succès rarissime, jamais de crash")
        _ = o
    }
}
