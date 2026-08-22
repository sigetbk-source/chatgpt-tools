@preconcurrency import UserNotifications
import Combine
import Foundation

enum NotificationAuthorizationState: Equatable {
    case notDetermined
    case denied
    case authorized
}

@MainActor
protocol UserNotificationCenterClient: AnyObject {
    var delegate: UNUserNotificationCenterDelegate? { get set }
    func authorizationStatus() async -> UNAuthorizationStatus
    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
    func add(_ request: UNNotificationRequest) async throws
    func removePendingNotificationRequests(withIdentifiers identifiers: [String])
    func removeDeliveredNotifications(withIdentifiers identifiers: [String])
    func pendingNotificationRequests() async -> [UNNotificationRequest]
}

@MainActor
final class SystemNotificationCenterClient: UserNotificationCenterClient {
    private let center: UNUserNotificationCenter

    init(center: UNUserNotificationCenter = .current()) {
        self.center = center
    }

    var delegate: UNUserNotificationCenterDelegate? {
        get { center.delegate }
        set { center.delegate = newValue }
    }

    func authorizationStatus() async -> UNAuthorizationStatus {
        await center.notificationSettings().authorizationStatus
    }

    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool {
        try await center.requestAuthorization(options: options)
    }

    func add(_ request: UNNotificationRequest) async throws {
        try await center.add(request)
    }

    func removePendingNotificationRequests(withIdentifiers identifiers: [String]) {
        center.removePendingNotificationRequests(withIdentifiers: identifiers)
    }

    func removeDeliveredNotifications(withIdentifiers identifiers: [String]) {
        center.removeDeliveredNotifications(withIdentifiers: identifiers)
    }

    func pendingNotificationRequests() async -> [UNNotificationRequest] {
        await center.pendingNotificationRequests()
    }
}

@MainActor
final class NotificationManager: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    static let eventIDKey = "ticketEventID"

    @Published private(set) var authorizationState: NotificationAuthorizationState = .notDetermined
    @Published private(set) var pendingEventID: UUID?

    private let center: UserNotificationCenterClient
    private let calendar: Calendar
    private var eventOperations: [UUID: Task<Int, Error>] = [:]
    private var eventOperationTokens: [UUID: UUID] = [:]
    private var desiredEvents: [UUID: TicketEvent] = [:]
    private var desiredRevision: UInt64 = 0

    init(
        center: UserNotificationCenterClient? = nil,
        calendar: Calendar = .current
    ) {
        self.center = center ?? SystemNotificationCenterClient()
        self.calendar = calendar
        super.init()
        self.center.delegate = self
    }

    func refreshAuthorizationState() async {
        authorizationState = Self.authorizationState(for: await center.authorizationStatus())
    }

    func requestAuthorizationIfNeeded() async -> Bool {
        await refreshAuthorizationState()
        switch authorizationState {
        case .authorized:
            return true
        case .denied:
            return false
        case .notDetermined:
            do {
                let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
                await refreshAuthorizationState()
                return granted
            } catch {
                await refreshAuthorizationState()
                return false
            }
        }
    }

    @discardableResult
    func scheduleNotifications(
        for event: TicketEvent,
        now: Date = Date(),
        stateRevision: UInt64 = 0
    ) async throws -> Int {
        guard stateRevision >= desiredRevision else {
            throw CancellationError()
        }
        desiredRevision = stateRevision
        desiredEvents[event.id] = event
        return try await scheduleNotifications(for: event, now: now)
    }

    private func scheduleNotifications(
        for event: TicketEvent,
        now: Date
    ) async throws -> Int {
        let plans = TicketNotificationPlan.pendingPlans(for: event, now: now)
        let identifiers = TicketNotificationPlan.removableIdentifiers(eventID: event.id)
        let calendar = calendar
        let center = center

        return try await enqueueOperation(for: event.id) {
            center.removePendingNotificationRequests(withIdentifiers: identifiers)
            center.removeDeliveredNotifications(withIdentifiers: identifiers)

            do {
                for plan in plans {
                    try Task.checkCancellation()

                    let content = UNMutableNotificationContent()
                    content.title = plan.title
                    content.body = "タップして発売待機を開始"
                    content.sound = .default
                    content.userInfo = [Self.eventIDKey: plan.eventID.uuidString]

                    let components = calendar.dateComponents(
                        [.calendar, .timeZone, .year, .month, .day, .hour, .minute, .second],
                        from: plan.fireDate
                    )
                    try await center.add(
                        UNNotificationRequest(
                            identifier: plan.identifier,
                            content: content,
                            trigger: UNCalendarNotificationTrigger(
                                dateMatching: components,
                                repeats: false
                            )
                        )
                    )
                }
                try Task.checkCancellation()
            } catch {
                center.removePendingNotificationRequests(withIdentifiers: identifiers)
                center.removeDeliveredNotifications(withIdentifiers: identifiers)
                throw error
            }
            return plans.count
        }
    }

    func cancelNotifications(for eventID: UUID, stateRevision: UInt64 = 0) async {
        guard stateRevision >= desiredRevision else {
            return
        }
        desiredRevision = stateRevision
        desiredEvents[eventID] = nil
        let identifiers = TicketNotificationPlan.removableIdentifiers(eventID: eventID)
        let center = center

        _ = try? await enqueueOperation(for: eventID) {
            center.removePendingNotificationRequests(withIdentifiers: identifiers)
            center.removeDeliveredNotifications(withIdentifiers: identifiers)
            return 0
        }
    }

    @discardableResult
    func reconcileNotifications(
        for events: [TicketEvent],
        now: Date = Date(),
        stateRevision: UInt64 = 0
    ) async -> Int {
        guard authorizationState == .authorized else {
            return 0
        }
        guard stateRevision >= desiredRevision else {
            return 0
        }

        let activeEvents = events.filter { $0.saleDate > now }
        desiredRevision = stateRevision
        desiredEvents = Dictionary(uniqueKeysWithValues: activeEvents.map { ($0.id, $0) })
        let pendingRequests = await center.pendingNotificationRequests()
        guard desiredRevision == stateRevision else {
            return 0
        }
        let pendingRequestsByIdentifier = Dictionary(
            uniqueKeysWithValues: pendingRequests.map { ($0.identifier, $0) }
        )
        let orphanedIdentifiers = pendingRequests.compactMap { request -> String? in
            guard
                let rawID = request.content.userInfo[Self.eventIDKey] as? String,
                let eventID = UUID(uuidString: rawID),
                desiredEvents[eventID] == nil
            else {
                return nil
            }
            return request.identifier
        }
        if !orphanedIdentifiers.isEmpty {
            center.removePendingNotificationRequests(withIdentifiers: orphanedIdentifiers)
            center.removeDeliveredNotifications(withIdentifiers: orphanedIdentifiers)
        }

        var failureCount = 0
        for event in activeEvents {
            guard
                desiredRevision == stateRevision,
                desiredEvents[event.id]?.updatedAt == event.updatedAt
            else {
                continue
            }
            let plans = TicketNotificationPlan.pendingPlans(for: event, now: now)
            let expectedIdentifiers = Set(plans.map(\.identifier))
            let currentIdentifiers = Set(
                pendingRequests.compactMap { request in
                    Self.eventID(from: request.content.userInfo) == event.id
                        ? request.identifier
                        : nil
                }
            )
            let notificationsAreCurrent = currentIdentifiers == expectedIdentifiers
                && plans.allSatisfy { plan in
                    guard let request = pendingRequestsByIdentifier[plan.identifier] else {
                        return false
                    }
                    return notificationRequest(request, matches: plan)
                }
            guard !notificationsAreCurrent else {
                continue
            }
            do {
                _ = try await scheduleNotifications(for: event, now: now)
            } catch {
                if !(error is CancellationError) {
                    failureCount += 1
                }
            }
        }
        return failureCount
    }

    private func notificationRequest(
        _ request: UNNotificationRequest,
        matches plan: TicketNotificationPlan
    ) -> Bool {
        guard
            Self.eventID(from: request.content.userInfo) == plan.eventID,
            request.content.title == plan.title,
            request.content.body == "タップして発売待機を開始",
            let trigger = request.trigger as? UNCalendarNotificationTrigger
        else {
            return false
        }
        let expectedComponents = calendar.dateComponents(
            [.calendar, .timeZone, .year, .month, .day, .hour, .minute, .second],
            from: plan.fireDate
        )
        return trigger.dateComponents == expectedComponents
    }

    func clearPendingEvent() {
        pendingEventID = nil
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        deliverForegroundNotificationOptions(completion: completionHandler)
    }

    nonisolated func deliverForegroundNotificationOptions(
        completion: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        DispatchQueue.main.async {
            completion([.banner, .list, .sound])
        }
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        guard let eventID = Self.eventID(from: response.notification.request.content.userInfo) else {
            DispatchQueue.main.async(execute: completionHandler)
            return
        }
        deliverNotificationEventID(eventID, completion: completionHandler)
    }

    nonisolated func deliverNotificationEventID(
        _ eventID: UUID,
        completion: @escaping () -> Void
    ) {
        DispatchQueue.main.async { [weak self] in
            self?.pendingEventID = eventID
            completion()
        }
    }

    nonisolated static func eventID(from userInfo: [AnyHashable: Any]) -> UUID? {
        guard let rawID = userInfo["ticketEventID"] as? String else {
            return nil
        }
        return UUID(uuidString: rawID)
    }

    private func enqueueOperation(
        for eventID: UUID,
        action: @escaping @MainActor () async throws -> Int
    ) async throws -> Int {
        let previousOperation = eventOperations[eventID]
        previousOperation?.cancel()
        let token = UUID()

        let operation = Task { @MainActor in
            if let previousOperation {
                _ = try? await previousOperation.value
            }
            try Task.checkCancellation()
            return try await action()
        }
        eventOperations[eventID] = operation
        eventOperationTokens[eventID] = token

        do {
            let result = try await operation.value
            clearOperation(for: eventID, token: token)
            return result
        } catch {
            clearOperation(for: eventID, token: token)
            throw error
        }
    }

    private func clearOperation(for eventID: UUID, token: UUID) {
        guard eventOperationTokens[eventID] == token else {
            return
        }
        eventOperations[eventID] = nil
        eventOperationTokens[eventID] = nil
    }

    private static func authorizationState(
        for status: UNAuthorizationStatus
    ) -> NotificationAuthorizationState {
        switch status {
        case .authorized, .provisional, .ephemeral:
            .authorized
        case .denied:
            .denied
        case .notDetermined:
            .notDetermined
        @unknown default:
            .denied
        }
    }
}
