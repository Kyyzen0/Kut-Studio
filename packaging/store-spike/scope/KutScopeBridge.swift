// Signets à portée de sécurité (ADR-0013 étape 5). Interface C : kutscope.h.
import Foundation

private let maxPathBytes = 4096
private let maxBookmarkChars = 65536

private let lock = NSLock()
private var nextHandle: Int64 = 1
private var openURLs: [Int64: URL] = [:]

private func give(_ text: String, _ out: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) {
    out?.pointee = strdup(text)
}

private func json(_ object: [String: Any]) -> String {
    guard JSONSerialization.isValidJSONObject(object),
          let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else { return "{}" }
    return text
}

@_cdecl("kutscope_abi_version")
public func kutscope_abi_version() -> Int32 { 1 }

@_cdecl("kutscope_bookmark")
public func kutscope_bookmark(_ path: UnsafePointer<CChar>?, _ out: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32 {
    out?.pointee = nil
    guard let path = path else { return 1 }
    let length = strnlen(path, maxPathBytes + 1)
    guard length > 0, length <= maxPathBytes, let text = String(validatingUTF8: path) else { return 1 }
    let url = URL(fileURLWithPath: text)
    do {
        let data = try url.bookmarkData(options: [.withSecurityScope], includingResourceValuesForKeys: nil, relativeTo: nil)
        give(data.base64EncodedString(), out)
        return 0
    } catch {
        return 2        // le détail du système peut citer le chemin : non renvoyé
    }
}

@_cdecl("kutscope_open")
public func kutscope_open(_ bookmark: UnsafePointer<CChar>?, _ handle: UnsafeMutablePointer<Int64>?,
                          _ out: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>?) -> Int32 {
    out?.pointee = nil
    handle?.pointee = 0
    guard let bookmark = bookmark, let handle = handle else { return 1 }
    let length = strnlen(bookmark, maxBookmarkChars + 1)
    guard length > 0, length <= maxBookmarkChars, let text = String(validatingUTF8: bookmark),
          let data = Data(base64Encoded: text) else { return 1 }
    var stale = false
    let url: URL
    do {
        url = try URL(resolvingBookmarkData: data, options: [.withSecurityScope], relativeTo: nil, bookmarkDataIsStale: &stale)
    } catch {
        return 3
    }
    guard url.startAccessingSecurityScopedResource() else { return 2 }
    var refreshed: Any = NSNull()
    if stale, let fresh = try? url.bookmarkData(options: [.withSecurityScope], includingResourceValuesForKeys: nil, relativeTo: nil) {
        refreshed = fresh.base64EncodedString()
    }
    lock.lock()
    let id = nextHandle
    nextHandle += 1
    openURLs[id] = url
    lock.unlock()
    handle.pointee = id
    give(json(["path": url.path, "stale": stale, "refreshed": refreshed]), out)
    return 0
}

@_cdecl("kutscope_close")
public func kutscope_close(_ handle: Int64) -> Int32 {
    lock.lock()
    let url = openURLs.removeValue(forKey: handle)
    lock.unlock()
    guard let url = url else { return 1 }
    url.stopAccessingSecurityScopedResource()
    return 0
}

@_cdecl("kutscope_open_count")
public func kutscope_open_count() -> Int32 {
    lock.lock(); defer { lock.unlock() }
    return Int32(openURLs.count)
}

@_cdecl("kutscope_free")
public func kutscope_free(_ pointer: UnsafeMutablePointer<CChar>?) { free(pointer) }
