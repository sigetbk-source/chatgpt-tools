import SwiftUI

@main
struct TicketLauncherApp: App {
    @StateObject private var store: EventStore
    @StateObject private var notifications: NotificationManager

    init() {
        _store = StateObject(wrappedValue: EventStore())
        _notifications = StateObject(wrappedValue: NotificationManager())
    }

    var body: some Scene {
        WindowGroup {
            ContentView(store: store, notifications: notifications)
                .environment(\.locale, Locale(identifier: "ja_JP"))
        }
    }
}
