import XCTest
@testable import TicketLauncher

@MainActor
final class TicketDateSelectionTests: XCTestCase {
    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Tokyo")!
        return calendar
    }

    func testExactFiveMinuteBoundaryIsPreserved() {
        let date = makeDate(hour: 10, minute: 5, second: 0)

        XCTAssertEqual(
            TicketDateSelection.roundedUp(date, calendar: calendar),
            date
        )
    }

    func testMinuteIsRoundedUpToNextFiveMinuteBoundary() {
        XCTAssertEqual(
            TicketDateSelection.roundedUp(
                makeDate(hour: 10, minute: 2, second: 0),
                calendar: calendar
            ),
            makeDate(hour: 10, minute: 5, second: 0)
        )
    }

    func testSecondsOnBoundaryRoundUpToNextBoundary() {
        XCTAssertEqual(
            TicketDateSelection.roundedUp(
                makeDate(hour: 10, minute: 5, second: 1),
                calendar: calendar
            ),
            makeDate(hour: 10, minute: 10, second: 0)
        )
    }

    func testRoundingCarriesIntoNextDay() {
        XCTAssertEqual(
            TicketDateSelection.roundedUp(
                makeDate(day: 3, hour: 23, minute: 58, second: 0),
                calendar: calendar
            ),
            makeDate(day: 4, hour: 0, minute: 0, second: 0)
        )
    }

    func testPastExistingDateIsPreservedForEditing() {
        let now = makeDate(hour: 10, minute: 0, second: 0)
        let existing = makeDate(hour: 9, minute: 58, second: 12)

        XCTAssertEqual(
            TicketDateSelection.initialEditorDate(
                existingDate: existing,
                now: now,
                calendar: calendar
            ),
            existing
        )
    }

    func testUpcomingExistingDateIsRoundedUpForEditing() {
        let now = makeDate(hour: 10, minute: 0, second: 0)

        XCTAssertEqual(
            TicketDateSelection.initialEditorDate(
                existingDate: makeDate(hour: 10, minute: 2, second: 0),
                now: now,
                calendar: calendar
            ),
            makeDate(hour: 10, minute: 5, second: 0)
        )
    }

    func testPickerValueRemovesHiddenSeconds() {
        XCTAssertEqual(
            TicketDateSelection.normalizedPickerValue(
                makeDate(hour: 10, minute: 5, second: 12),
                calendar: calendar
            ),
            makeDate(hour: 10, minute: 5, second: 0)
        )
    }

    func testPickerValueFallsBackToDisplayedFiveMinuteBoundary() {
        XCTAssertEqual(
            TicketDateSelection.normalizedPickerValue(
                makeDate(hour: 10, minute: 7, second: 12),
                calendar: calendar
            ),
            makeDate(hour: 10, minute: 5, second: 0)
        )
    }

    private func makeDate(
        day: Int = 3,
        hour: Int,
        minute: Int,
        second: Int
    ) -> Date {
        calendar.date(
            from: DateComponents(
                year: 2026,
                month: 9,
                day: day,
                hour: hour,
                minute: minute,
                second: second
            )
        )!
    }
}
