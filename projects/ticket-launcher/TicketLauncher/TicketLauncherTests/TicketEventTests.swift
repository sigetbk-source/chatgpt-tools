import XCTest
@testable import TicketLauncher

@MainActor
final class TicketEventTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testValidInputIsTrimmed() throws {
        let input = TicketEventInput(
            name: "  ライブ  ",
            saleDate: now.addingTimeInterval(600),
            saleURLText: "  https://example.com/tickets  ",
            memo: "  先着  "
        )

        let event = try TicketEventValidator.validatedEvent(from: input, now: now)

        XCTAssertEqual(event.name, "ライブ")
        XCTAssertEqual(event.saleURL.absoluteString, "https://example.com/tickets")
        XCTAssertEqual(event.memo, "先着")
    }

    func testEmptyNameIsRejected() {
        let input = validInput(name: " \n ")

        XCTAssertThrowsError(try TicketEventValidator.validatedEvent(from: input, now: now)) { error in
            XCTAssertEqual(error as? TicketEventValidationError, .emptyName)
        }
    }

    func testNonHTTPSURLIsRejected() {
        let input = validInput(saleURLText: "http://example.com/tickets")

        XCTAssertThrowsError(try TicketEventValidator.validatedEvent(from: input, now: now)) { error in
            XCTAssertEqual(error as? TicketEventValidationError, .invalidURL)
        }
    }

    func testPastSaleDateIsRejected() {
        let input = validInput(saleDate: now)

        XCTAssertThrowsError(try TicketEventValidator.validatedEvent(from: input, now: now)) { error in
            XCTAssertEqual(error as? TicketEventValidationError, .pastSaleDate)
        }
    }

    func testEditingPreservesIdentityAndCreationDate() throws {
        let original = try TicketEventValidator.validatedEvent(from: validInput(), now: now)
        let edited = try TicketEventValidator.validatedEvent(
            from: validInput(name: "変更後"),
            existing: original,
            now: now.addingTimeInterval(60)
        )

        XCTAssertEqual(edited.id, original.id)
        XCTAssertEqual(edited.createdAt, original.createdAt)
        XCTAssertGreaterThan(edited.updatedAt, original.updatedAt)
    }

    func testPastEventCanBeEditedWithoutChangingSaleDate() throws {
        let original = TicketEvent(
            name: "発売済み",
            saleDate: now.addingTimeInterval(-600),
            saleURL: URL(string: "https://example.com/tickets")!,
            createdAt: now.addingTimeInterval(-1_000),
            updatedAt: now.addingTimeInterval(-1_000)
        )
        let input = TicketEventInput(
            name: "発売済み（更新）",
            saleDate: original.saleDate,
            saleURLText: original.saleURL.absoluteString,
            memo: "結果を追記"
        )

        let edited = try TicketEventValidator.validatedEvent(
            from: input,
            existing: original,
            now: now
        )

        XCTAssertEqual(edited.id, original.id)
        XCTAssertEqual(edited.saleDate, original.saleDate)
        XCTAssertEqual(edited.memo, "結果を追記")
    }

    func testUpcomingEventCannotBeEditedToPastDate() throws {
        let original = try TicketEventValidator.validatedEvent(from: validInput(), now: now)
        let input = validInput(saleDate: now.addingTimeInterval(-1))

        XCTAssertThrowsError(
            try TicketEventValidator.validatedEvent(from: input, existing: original, now: now)
        ) { error in
            XCTAssertEqual(error as? TicketEventValidationError, .pastSaleDate)
        }
    }

    private func validInput(
        name: String = "ライブ",
        saleDate: Date? = nil,
        saleURLText: String = "https://example.com/tickets"
    ) -> TicketEventInput {
        TicketEventInput(
            name: name,
            saleDate: saleDate ?? now.addingTimeInterval(600),
            saleURLText: saleURLText,
            memo: ""
        )
    }
}
