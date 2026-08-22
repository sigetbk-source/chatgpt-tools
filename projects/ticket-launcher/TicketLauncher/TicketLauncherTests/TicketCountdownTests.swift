import XCTest
@testable import TicketLauncher

final class TicketCountdownTests: XCTestCase {
    private let saleDate = Date(timeIntervalSince1970: 1_800_001_000)

    func testWaitsBeforeSaleTime() {
        XCTAssertEqual(
            TicketCountdownPolicy.decision(
                saleDate: saleDate,
                now: saleDate.addingTimeInterval(-3),
                isSceneActive: true,
                wasInterrupted: false,
                hasOpenedSafari: false
            ),
            .waiting(3)
        )
    }

    func testOpensSafariAtSaleTime() {
        XCTAssertEqual(
            TicketCountdownPolicy.decision(
                saleDate: saleDate,
                now: saleDate,
                isSceneActive: true,
                wasInterrupted: false,
                hasOpenedSafari: false
            ),
            .openSafari
        )
    }

    func testInterruptionRequiresManualOpen() {
        XCTAssertEqual(
            TicketCountdownPolicy.decision(
                saleDate: saleDate,
                now: saleDate.addingTimeInterval(-3),
                isSceneActive: true,
                wasInterrupted: true,
                hasOpenedSafari: false
            ),
            .manualOpenRequired
        )
    }

    func testAlreadyOpenedSessionDoesNotOpenAgain() {
        XCTAssertEqual(
            TicketCountdownPolicy.decision(
                saleDate: saleDate,
                now: saleDate.addingTimeInterval(1),
                isSceneActive: true,
                wasInterrupted: false,
                hasOpenedSafari: true
            ),
            .completed
        )
    }

    func testInactiveSceneAtSaleTimeRequiresManualOpen() {
        XCTAssertEqual(
            TicketCountdownPolicy.decision(
                saleDate: saleDate,
                now: saleDate,
                isSceneActive: false,
                wasInterrupted: false,
                hasOpenedSafari: false
            ),
            .manualOpenRequired
        )
    }

    func testSameEventCreatesIndependentWaitingSessions() {
        let event = TicketEvent(
            name: "ライブ",
            saleDate: saleDate,
            saleURL: URL(string: "https://example.com/tickets")!
        )

        XCTAssertNotEqual(
            TicketWaitingSession(event: event).id,
            TicketWaitingSession(event: event).id
        )
    }

    func testIdleTimerLeaseRestoresInitiallyEnabledTimer() {
        var lease = IdleTimerLease()

        XCTAssertEqual(lease.acquire(currentValue: false), true)
        XCTAssertNil(lease.acquire(currentValue: true))
        XCTAssertEqual(lease.release(), false)
        XCTAssertNil(lease.release())
    }

    func testIdleTimerLeasePreservesInitiallyDisabledTimer() {
        var lease = IdleTimerLease()

        XCTAssertEqual(lease.acquire(currentValue: true), true)
        XCTAssertEqual(lease.release(), true)
    }
}
