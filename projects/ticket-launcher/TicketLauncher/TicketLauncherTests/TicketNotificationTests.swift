import XCTest
@testable import TicketLauncher

@MainActor
final class TicketNotificationTests: XCTestCase {
    private let saleDate = Date(timeIntervalSince1970: 1_800_001_000)

    func testAllNotificationsArePlannedWhenRegisteredEarly() {
        let event = makeEvent()

        let plans = TicketNotificationPlan.pendingPlans(
            for: event,
            now: saleDate.addingTimeInterval(-900)
        )

        XCTAssertEqual(plans.map(\.timing), TicketNotificationTiming.allCases)
        XCTAssertEqual(
            plans.map(\.fireDate),
            [
                saleDate.addingTimeInterval(-600),
                saleDate.addingTimeInterval(-300),
                saleDate.addingTimeInterval(-180),
                saleDate.addingTimeInterval(-60)
            ]
        )
    }

    func testOnlyOneMinuteNotificationIsPlannedAfterThreeMinuteWindow() {
        let plans = TicketNotificationPlan.pendingPlans(
            for: makeEvent(),
            now: saleDate.addingTimeInterval(-120)
        )

        XCTAssertEqual(plans.map(\.timing), [.oneMinute])
    }

    func testNoNotificationIsPlannedAfterOneMinuteWindow() {
        let plans = TicketNotificationPlan.pendingPlans(
            for: makeEvent(),
            now: saleDate.addingTimeInterval(-30)
        )

        XCTAssertTrue(plans.isEmpty)
    }

    func testNoNotificationIsPlannedAtOrAfterSaleTime() {
        XCTAssertTrue(
            TicketNotificationPlan.pendingPlans(for: makeEvent(), now: saleDate).isEmpty
        )
    }

    func testIdentifiersAreUniqueForEveryTiming() {
        let identifiers = TicketNotificationPlan.identifiers(eventID: makeEvent().id)

        XCTAssertEqual(identifiers.count, 4)
        XCTAssertEqual(Set(identifiers).count, 4)
    }

    func testTitlesMatchSpecification() {
        let titles = TicketNotificationPlan.pendingPlans(
            for: makeEvent(),
            now: saleDate.addingTimeInterval(-900)
        ).map(\.title)

        XCTAssertEqual(
            titles,
            [
                "発売10分前：ライブ",
                "まもなく発売：ライブ",
                "準備開始：ライブ",
                "まもなく開始：ライブ"
            ]
        )
    }

    private func makeEvent() -> TicketEvent {
        TicketEvent(
            id: UUID(uuidString: "11111111-1111-1111-1111-111111111111")!,
            name: "ライブ",
            saleDate: saleDate,
            saleURL: URL(string: "https://example.com/tickets")!
        )
    }
}
