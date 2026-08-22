import XCTest
@testable import TicketLauncher

@MainActor
final class EventStoreTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "EventStoreTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
        super.tearDown()
    }

    func testSavedEventsAreRestored() throws {
        let store = EventStore(defaults: defaults)
        let event = makeEvent(name: "保存確認", saleDate: Date().addingTimeInterval(600))

        try store.save(event)
        let restoredStore = EventStore(defaults: defaults)

        XCTAssertEqual(restoredStore.events, [event])
    }

    func testSavingSameIDUpdatesInsteadOfDuplicating() throws {
        let store = EventStore(defaults: defaults)
        var event = makeEvent(name: "変更前", saleDate: Date().addingTimeInterval(600))
        try store.save(event)
        event.name = "変更後"

        try store.save(event)

        XCTAssertEqual(store.events.count, 1)
        XCTAssertEqual(store.events.first?.name, "変更後")
        XCTAssertEqual(store.revision, 2)
    }

    func testDeletePersists() throws {
        let store = EventStore(defaults: defaults)
        let event = makeEvent(name: "削除", saleDate: Date().addingTimeInterval(600))
        try store.save(event)

        try store.delete(event)

        XCTAssertTrue(EventStore(defaults: defaults).events.isEmpty)
        XCTAssertEqual(store.revision, 2)
    }

    func testUpcomingAndPastEventsHaveExpectedOrdering() throws {
        let referenceDate = Date(timeIntervalSince1970: 1_800_000_000)
        let store = EventStore(defaults: defaults)
        try store.save(makeEvent(name: "未来2", saleDate: referenceDate.addingTimeInterval(200)))
        try store.save(makeEvent(name: "過去2", saleDate: referenceDate.addingTimeInterval(-200)))
        try store.save(makeEvent(name: "未来1", saleDate: referenceDate.addingTimeInterval(100)))
        try store.save(makeEvent(name: "過去1", saleDate: referenceDate.addingTimeInterval(-100)))

        XCTAssertEqual(store.upcomingEvents(at: referenceDate).map(\.name), ["未来1", "未来2"])
        XCTAssertEqual(store.pastEvents(at: referenceDate).map(\.name), ["過去1", "過去2"])
    }

    private func makeEvent(name: String, saleDate: Date) -> TicketEvent {
        TicketEvent(
            name: name,
            saleDate: saleDate,
            saleURL: URL(string: "https://example.com/tickets")!
        )
    }
}
