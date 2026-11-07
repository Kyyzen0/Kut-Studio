// Application hôte minimale : donne au processus de test un identifiant de bundle connu de LaunchServices
// (StoreKitTest refuse le processus nu `xctest` : « Unable to get entitlements for com.apple.dt.xctest.tool »)
// et une fenêtre (Product.purchase(confirmIn:) l'exige : sinon « invalidPresentationContext »).
import AppKit

final class HostDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: -3000, y: -3000, width: 200, height: 100),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.contentViewController = NSViewController()
        window.contentViewController?.view = NSView(frame: NSRect(x: 0, y: 0, width: 200, height: 100))
        window.orderFrontRegardless()
    }
}
let app = NSApplication.shared
let delegate = HostDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
