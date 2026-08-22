import SwiftUI

struct TicketWaitingSession: Identifiable, Equatable {
    let id = UUID()
    let event: TicketEvent
}

struct ContentView: View {
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase

    @StateObject private var store: EventStore
    @ObservedObject private var notifications: NotificationManager
    @State private var editorItem: EditorItem?
    @State private var waitingSession: TicketWaitingSession?
    @State private var errorMessage: String?

    init(
        store: EventStore? = nil,
        notifications: NotificationManager? = nil
    ) {
        _store = StateObject(wrappedValue: store ?? EventStore())
        _notifications = ObservedObject(wrappedValue: notifications ?? NotificationManager())
    }

    var body: some View {
        NavigationStack {
            Group {
                if store.events.isEmpty {
                    ContentUnavailableView(
                        "チケット予定はありません",
                        systemImage: "ticket",
                        description: Text("右上の＋から発売予定を登録できます。")
                    )
                } else {
                    eventList
                }
            }
            .navigationTitle("Ticket Launcher")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button("イベントを追加", systemImage: "plus") {
                        editorItem = EditorItem(event: nil)
                    }
                }
            }
            .sheet(item: $editorItem) { item in
                EventEditorView(event: item.event) { event in
                    save(event)
                }
            }
            .fullScreenCover(item: $waitingSession) { session in
                TicketCountdownView(event: session.event) { url in
                    openURL(url)
                }
                .id(session.id)
            }
            .alert("エラー", isPresented: errorBinding) {
                Button("閉じる", role: .cancel) {}
            } message: {
                Text(errorMessage ?? "不明なエラーです。")
            }
            .task {
                if !presentPendingWaitingSessionIfNeeded() {
                    await synchronizeNotifications()
                }
            }
            .onChange(of: notifications.pendingEventID) {
                presentPendingWaitingSessionIfNeeded()
            }
            .onChange(of: scenePhase) {
                guard scenePhase == .active else { return }
                if !presentPendingWaitingSessionIfNeeded() {
                    Task {
                        await synchronizeNotifications()
                    }
                }
            }
        }
    }

    private var eventList: some View {
        List {
            if notifications.authorizationState != .authorized {
                Section {
                    Label(notificationStatusMessage, systemImage: "bell.slash")
                        .foregroundStyle(.secondary)
                }
            }

            if !store.upcomingEvents.isEmpty {
                Section("発売予定") {
                    ForEach(store.upcomingEvents) { event in
                        eventRow(event, isPast: false)
                    }
                    .onDelete { offsets in
                        delete(offsets, from: store.upcomingEvents)
                    }
                }
            }

            if !store.pastEvents.isEmpty {
                Section("発売済み") {
                    ForEach(store.pastEvents) { event in
                        eventRow(event, isPast: true)
                    }
                    .onDelete { offsets in
                        delete(offsets, from: store.pastEvents)
                    }
                }
            }
        }
    }

    private func eventRow(_ event: TicketEvent, isPast: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                editorItem = EditorItem(event: event)
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(event.name)
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Text(event.saleDate, format: .dateTime.year().month().day().hour().minute())
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    if !event.memo.isEmpty {
                        Text(event.memo)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)

            Button {
                if isPast {
                    openURL(event.saleURL)
                } else {
                    waitingSession = TicketWaitingSession(event: event)
                }
            } label: {
                Label(isPast ? "販売ページを確認" : "発売待機を開始", systemImage: isPast ? "safari" : "timer")
                    .font(.subheadline.weight(.semibold))
            }
        }
        .padding(.vertical, 4)
    }

    private func delete(_ offsets: IndexSet, from displayedEvents: [TicketEvent]) {
        do {
            for index in offsets {
                let event = displayedEvents[index]
                try store.delete(event)
                let revision = store.revision
                Task {
                    await notifications.cancelNotifications(
                        for: event.id,
                        stateRevision: revision
                    )
                }
            }
        } catch {
            errorMessage = "削除結果を保存できませんでした。"
        }
    }

    private var notificationStatusMessage: String {
        switch notifications.authorizationState {
        case .notDetermined:
            "最初の保存時に通知の許可を確認します。"
        case .denied:
            "通知がオフです。iPhoneの設定から許可してください。"
        case .authorized:
            ""
        }
    }

    private func save(_ event: TicketEvent) {
        do {
            try store.save(event)
            editorItem = nil
        } catch {
            errorMessage = "保存できませんでした。もう一度お試しください。"
            return
        }

        Task {
            guard await notifications.requestAuthorizationIfNeeded() else {
                errorMessage = "イベントは保存しましたが、通知はオフです。iPhoneの設定から通知を許可してください。"
                return
            }
            guard
                store.event(id: event.id)?.updatedAt == event.updatedAt
            else {
                return
            }
            do {
                try await notifications.scheduleNotifications(
                    for: event,
                    stateRevision: store.revision
                )
            } catch {
                guard !(error is CancellationError) else { return }
                errorMessage = "イベントは保存しましたが、通知を予約できませんでした。"
            }
        }
    }

    private func synchronizeNotifications() async {
        let events = store.events
        let revision = store.revision
        await notifications.refreshAuthorizationState()
        let failureCount = await notifications.reconcileNotifications(
            for: events,
            stateRevision: revision
        )
        if failureCount > 0 {
            errorMessage = "一部の通知を再設定できませんでした。イベントを開いて保存し直してください。"
        }
    }

    @discardableResult
    private func presentPendingWaitingSessionIfNeeded() -> Bool {
        guard scenePhase == .active else {
            return false
        }
        guard let eventID = notifications.pendingEventID else {
            return false
        }
        notifications.clearPendingEvent()
        guard let event = store.event(id: eventID) else {
            errorMessage = "通知のイベントは削除されているため、発売待機を開始できませんでした。"
            return true
        }
        waitingSession = TicketWaitingSession(event: event)
        return true
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )
    }
}

private struct EditorItem: Identifiable {
    let id = UUID()
    let event: TicketEvent?
}

private struct EventEditorView: View {
    @Environment(\.dismiss) private var dismiss

    let event: TicketEvent?
    let onSave: (TicketEvent) -> Void

    @State private var name: String
    @State private var saleDate: Date
    @State private var saleURLText: String
    @State private var memo: String
    @State private var validationMessage: String?

    init(event: TicketEvent?, onSave: @escaping (TicketEvent) -> Void) {
        self.event = event
        self.onSave = onSave
        _name = State(initialValue: event?.name ?? "")
        _saleDate = State(initialValue: event?.saleDate ?? Date().addingTimeInterval(3_600))
        _saleURLText = State(initialValue: event?.saleURL.absoluteString ?? "")
        _memo = State(initialValue: event?.memo ?? "")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("チケット情報") {
                    TextField("イベント名", text: $name)
                        .textInputAutocapitalization(.never)
                    DatePicker("発売日時", selection: $saleDate)
                    TextField("販売URL（https://）", text: $saleURLText)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("メモ（任意）", text: $memo, axis: .vertical)
                        .lineLimit(3...6)
                }

                if let validationMessage {
                    Section {
                        Text(validationMessage)
                            .foregroundStyle(.red)
                            .accessibilityLabel("入力エラー。\(validationMessage)")
                    }
                }
            }
            .navigationTitle(event == nil ? "イベントを追加" : "イベントを編集")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("キャンセル") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { save() }
                }
            }
        }
    }

    private func save() {
        let input = TicketEventInput(
            name: name,
            saleDate: saleDate,
            saleURLText: saleURLText,
            memo: memo
        )

        do {
            validationMessage = nil
            onSave(try TicketEventValidator.validatedEvent(from: input, existing: event))
        } catch let error as TicketEventValidationError {
            validationMessage = error.errorDescription
        } catch {
            validationMessage = "入力内容を確認してください。"
        }
    }
}

#Preview {
    ContentView(
        store: EventStore(defaults: UserDefaults(suiteName: "preview")!),
        notifications: NotificationManager()
    )
}
