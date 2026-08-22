import XCTest
@testable import TicketLauncher

@MainActor
final class TicketNotificationTests: XCTestCase {
    private let saleDate = Date(timeIntervalSince1970: 1_800_001_000)

    func testBothNotificationsArePlannedWhenRegisteredEarly() {
        let event = makeEvent()

        let plans = TicketNotificationPlan.pendingPlans(
            for: event,
            now: saleDate.addingTimeInterval(-600)
        )

        XCTAssertEqual(plans.map(\.timing), TicketNotificationTiming.allCases)
        XCTAssertEqual(
            plans.map(\.fireDate),
            [
                saleDate.addingTimeInterval(-300),
                saleDate.addingTimeInterval(-180)
            ]
        )
    }

    func testNoNotificationIsPlannedAfterThreeMinuteWindow() {
        let plans = TicketNotificationPlan.pendingPlans(
            for: makeEvent(),
            now: saleDate.addingTimeInterval(-120)
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

        XCTAssertEqual(identifiers.count, 2)
        XCTAssertEqual(Set(identifiers).count, 2)
    }

    func testTitlesMatchSpecification() {
        let titles = TicketNotificationPlan.pendingPlans(
            for: makeEvent(),
            now: saleDate.addingTimeInterval(-600)
        ).map(\.title)

        XCTAssertEqual(
            titles,
            [
                "まもなく発売：ライブ",
                "準備開始：ライブ"
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
