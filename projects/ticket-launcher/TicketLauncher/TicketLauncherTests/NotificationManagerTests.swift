@preconcurrency import UserNotifications
import Combine
import XCTest
@testable import TicketLauncher

@MainActor
final class NotificationManagerTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testAuthorizedStateDoesNotRequestPermissionAgain() async {
        let center = FakeNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)

        let allowed = await manager.requestAuthorizationIfNeeded()

        XCTAssertTrue(allowed)
        XCTAssertEqual(center.requestAuthorizationCallCount, 0)
        XCTAssertEqual(manager.authorizationState, .authorized)
    }

    func testDeniedStateReturnsFalse() async {
        let center = FakeNotificationCenter(status: .denied)
        let manager = NotificationManager(center: center)

        let allowed = await manager.requestAuthorizationIfNeeded()

        XCTAssertFalse(allowed)
        XCTAssertEqual(center.requestAuthorizationCallCount, 0)
        XCTAssertEqual(manager.authorizationState, .denied)
    }

    func testPermissionRequestUpdatesState() async {
        let center = FakeNotificationCenter(status: .notDetermined)
        center.requestAuthorizationResult = true
        let manager = NotificationManager(center: center)

        let allowed = await manager.requestAuthorizationIfNeeded()

        XCTAssertTrue(allowed)
        XCTAssertEqual(center.requestAuthorizationCallCount, 1)
        XCTAssertEqual(manager.authorizationState, .authorized)
    }

    func testScheduleAddsFourRequestsAndRescheduleReplacesContent() async throws {
        let center = FakeNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        var event = makeEvent(name: "変更前")

        let firstCount = try await manager.scheduleNotifications(for: event, now: now)
        XCTAssertEqual(firstCount, 4)
        event.name = "変更後"
        let secondCount = try await manager.scheduleNotifications(for: event, now: now)
        XCTAssertEqual(secondCount, 4)

        XCTAssertEqual(center.requests.count, 4)
        XCTAssertTrue(center.requests.values.allSatisfy { $0.content.title.contains("変更後") })
        XCTAssertTrue(center.requests.values.allSatisfy {
            $0.content.body == "タップして発売待機を開始"
        })
    }

    func testCancelRemovesAllRequestsForEvent() async throws {
        let center = FakeNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        let event = makeEvent()
        try await manager.scheduleNotifications(for: event, now: now)

        await manager.cancelNotifications(for: event.id)

        XCTAssertTrue(center.requests.isEmpty)
    }

    func testPartialSchedulingFailureRollsBackAddedRequests() async {
        let center = FakeNotificationCenter(status: .authorized)
        center.failOnAddNumber = 2
        let manager = NotificationManager(center: center)

        do {
            try await manager.scheduleNotifications(for: makeEvent(), now: now)
            XCTFail("通知登録は失敗する必要があります")
        } catch {
            XCTAssertTrue(center.requests.isEmpty)
        }
    }

    func testReconcileRemovesOrphanAndSchedulesActiveEvent() async {
        let center = FakeNotificationCenter(status: .authorized)
        let orphan = makeEvent(id: UUID(), name: "削除済み")
        center.insertRequest(for: orphan)
        let active = makeEvent()
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()

        let failureCount = await manager.reconcileNotifications(for: [active], now: now)

        XCTAssertEqual(failureCount, 0)
        XCTAssertEqual(center.requests.count, 4)
        XCTAssertTrue(center.requests.values.allSatisfy {
            NotificationManager.eventID(from: $0.content.userInfo) == active.id
        })
    }

    func testReconcileReplacesStaleRequestsAndRemovesLegacySaleTimeRequest() async {
        let center = FakeNotificationCenter(status: .authorized)
        let event = makeEvent()
        center.insertLegacyRequests(for: event)
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()

        let failureCount = await manager.reconcileNotifications(for: [event], now: now)

        XCTAssertEqual(failureCount, 0)
        XCTAssertEqual(
            Set(center.requests.keys),
            Set(TicketNotificationPlan.identifiers(eventID: event.id))
        )
    }

    func testReconcileReportsSchedulingFailure() async {
        let center = FakeNotificationCenter(status: .authorized)
        center.failOnAddNumber = 1
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()

        let failureCount = await manager.reconcileNotifications(for: [makeEvent()], now: now)

        XCTAssertEqual(failureCount, 1)
        XCTAssertTrue(center.requests.isEmpty)
    }

    func testForegroundReconcileDoesNotRestoreConsumedReminderNotifications() async throws {
        let center = FakeNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()
        let event = makeEvent()
        try await manager.scheduleNotifications(for: event, now: now)
        let identifiers = TicketNotificationPlan.identifiers(eventID: event.id)
        center.removePendingNotificationRequests(withIdentifiers: Array(identifiers.prefix(2)))

        let failureCount = await manager.reconcileNotifications(
            for: [event],
            now: now.addingTimeInterval(601)
        )

        XCTAssertEqual(failureCount, 0)
        XCTAssertEqual(center.addCallCount, 4)
        XCTAssertEqual(
            Set(center.requests.keys),
            Set(identifiers.suffix(2))
        )
    }

    func testDeleteWhileReconcileIsSuspendedDoesNotRestoreNotifications() async {
        let center = FakeNotificationCenter(status: .authorized)
        center.suspendPendingRequestRead = true
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()
        let event = makeEvent()

        let reconcileTask = Task {
            await manager.reconcileNotifications(for: [event], now: now, stateRevision: 1)
        }
        while !center.isPendingRequestReadSuspended {
            await Task.yield()
        }

        await manager.cancelNotifications(for: event.id, stateRevision: 2)
        center.resumePendingRequestRead()
        _ = await reconcileTask.value

        XCTAssertTrue(center.requests.isEmpty)
    }

    func testEditWhileReconcileIsSuspendedKeepsNewestNotificationContent() async throws {
        let center = FakeNotificationCenter(status: .authorized)
        center.suspendPendingRequestRead = true
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()
        let oldEvent = makeEvent(name: "変更前")

        let reconcileTask = Task {
            await manager.reconcileNotifications(for: [oldEvent], now: now, stateRevision: 1)
        }
        while !center.isPendingRequestReadSuspended {
            await Task.yield()
        }

        var newEvent = oldEvent
        newEvent.name = "変更後"
        newEvent.updatedAt = oldEvent.updatedAt.addingTimeInterval(1)
        try await manager.scheduleNotifications(for: newEvent, now: now, stateRevision: 2)
        center.resumePendingRequestRead()
        _ = await reconcileTask.value

        XCTAssertEqual(center.requests.count, 4)
        XCTAssertTrue(center.requests.values.allSatisfy { $0.content.title.contains("変更後") })
    }

    func testNewEventWhileReconcileIsSuspendedIsNotRemovedAsOrphan() async throws {
        let center = FakeNotificationCenter(status: .authorized)
        center.suspendPendingRequestRead = true
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()

        let reconcileTask = Task {
            await manager.reconcileNotifications(for: [], now: now, stateRevision: 1)
        }
        while !center.isPendingRequestReadSuspended {
            await Task.yield()
        }

        let newEvent = makeEvent()
        try await manager.scheduleNotifications(for: newEvent, now: now, stateRevision: 2)
        center.resumePendingRequestRead()
        _ = await reconcileTask.value

        XCTAssertEqual(center.requests.count, 4)
    }

    func testOlderReconcileCannotOverwriteAlreadyScheduledNewerState() async throws {
        let center = FakeNotificationCenter(status: .authorized)
        let manager = NotificationManager(center: center)
        await manager.refreshAuthorizationState()
        let oldEvent = makeEvent(name: "変更前")
        var newEvent = oldEvent
        newEvent.name = "変更後"
        newEvent.updatedAt = oldEvent.updatedAt.addingTimeInterval(1)
        try await manager.scheduleNotifications(for: newEvent, now: now, stateRevision: 2)

        await manager.reconcileNotifications(for: [oldEvent], now: now, stateRevision: 1)

        XCTAssertTrue(center.requests.values.allSatisfy { $0.content.title.contains("変更後") })
    }

    func testEventIDParserRejectsMissingOrInvalidValues() {
        let eventID = UUID()

        XCTAssertEqual(
            NotificationManager.eventID(from: [NotificationManager.eventIDKey: eventID.uuidString]),
            eventID
        )
        XCTAssertNil(NotificationManager.eventID(from: [:]))
        XCTAssertNil(NotificationManager.eventID(from: [NotificationManager.eventIDKey: "invalid"]))
    }

    func testNotificationEventIDIsDeliveredOnceOnMainThread() async {
        let manager = NotificationManager(
            center: FakeNotificationCenter(status: .authorized)
        )
        let eventID = UUID()
        let delivered = expectation(description: "通知イベントIDが反映される")
        var receivedIDs: [UUID] = []
        var wasDeliveredOnMainThread = false
        let cancellable = manager.$pendingEventID
            .dropFirst()
            .sink { receivedID in
                guard let receivedID else { return }
                receivedIDs.append(receivedID)
                wasDeliveredOnMainThread = Thread.isMainThread
                delivered.fulfill()
            }

        await Task.detached {
            await withCheckedContinuation { continuation in
                manager.deliverNotificationEventID(eventID) {
                    continuation.resume()
                }
            }
        }.value
        await fulfillment(of: [delivered], timeout: 1)

        XCTAssertEqual(receivedIDs, [eventID])
        XCTAssertTrue(wasDeliveredOnMainThread)
        withExtendedLifetime(cancellable) {}
    }

    func testForegroundNotificationOptionsAreDeliveredOnceOnMainThread() async {
        let manager = NotificationManager(
            center: FakeNotificationCenter(status: .authorized)
        )
        var receivedOptions: [UNNotificationPresentationOptions] = []
        var wasDeliveredOnMainThread = false

        await Task.detached {
            await withCheckedContinuation { continuation in
                manager.deliverForegroundNotificationOptions { options in
                    receivedOptions.append(options)
                    wasDeliveredOnMainThread = Thread.isMainThread
                    continuation.resume()
                }
            }
        }.value

        XCTAssertEqual(receivedOptions.count, 1)
        XCTAssertEqual(receivedOptions.first, [.banner, .list, .sound])
        XCTAssertTrue(wasDeliveredOnMainThread)
    }

    private func makeEvent(
        id: UUID = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!,
        name: String = "ライブ"
    ) -> TicketEvent {
        TicketEvent(
            id: id,
            name: name,
            saleDate: now.addingTimeInterval(900),
            saleURL: URL(string: "https://example.com/tickets")!
        )
    }
}

@MainActor
private final class FakeNotificationCenter: UserNotificationCenterClient {
    enum TestError: Error {
        case addFailed
    }

    weak var delegate: UNUserNotificationCenterDelegate?
    var status: UNAuthorizationStatus
    var requestAuthorizationResult = false
    var requestAuthorizationCallCount = 0
    var failOnAddNumber: Int?
    var suspendPendingRequestRead = false
    private(set) var isPendingRequestReadSuspended = false
    private(set) var addCallCount = 0
    private(set) var requests: [String: UNNotificationRequest] = [:]
    private var pendingRequestReadContinuation: CheckedContinuation<Void, Never>?

    init(status: UNAuthorizationStatus) {
        self.status = status
    }

    func authorizationStatus() async -> UNAuthorizationStatus {
        status
    }

    func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool {
        requestAuthorizationCallCount += 1
        status = requestAuthorizationResult ? .authorized : .denied
        return requestAuthorizationResult
    }

    func add(_ request: UNNotificationRequest) async throws {
        addCallCount += 1
        if addCallCount == failOnAddNumber {
            throw TestError.addFailed
        }
        requests[request.identifier] = request
    }

    func removePendingNotificationRequests(withIdentifiers identifiers: [String]) {
        for identifier in identifiers {
            requests[identifier] = nil
        }
    }

    func removeDeliveredNotifications(withIdentifiers identifiers: [String]) {}

    func pendingNotificationRequests() async -> [UNNotificationRequest] {
        if suspendPendingRequestRead {
            isPendingRequestReadSuspended = true
            await withCheckedContinuation { continuation in
                pendingRequestReadContinuation = continuation
            }
            isPendingRequestReadSuspended = false
            suspendPendingRequestRead = false
        }
        return Array(requests.values)
    }

    func resumePendingRequestRead() {
        pendingRequestReadContinuation?.resume()
        pendingRequestReadContinuation = nil
    }

    func insertRequest(for event: TicketEvent) {
        let content = UNMutableNotificationContent()
        content.userInfo = [NotificationManager.eventIDKey: event.id.uuidString]
        let identifier = TicketNotificationPlan.identifiers(eventID: event.id)[0]
        requests[identifier] = UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: nil
        )
    }

    func insertLegacyRequests(for event: TicketEvent) {
        let content = UNMutableNotificationContent()
        content.userInfo = [NotificationManager.eventIDKey: event.id.uuidString]
        for suffix in ["one-minute", "sale-time"] {
            let identifier = "ticket.\(event.id.uuidString).\(suffix)"
            requests[identifier] = UNNotificationRequest(
                identifier: identifier,
                content: content,
                trigger: nil
            )
        }
    }
}
