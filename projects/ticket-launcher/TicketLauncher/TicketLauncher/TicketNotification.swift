import Foundation

enum TicketNotificationTiming: String, CaseIterable, Sendable {
    case tenMinutes = "ten-minutes"
    case fiveMinutes = "five-minutes"
    case threeMinutes = "three-minutes"
    case oneMinute = "one-minute"

    var offset: TimeInterval {
        switch self {
        case .tenMinutes: -600
        case .fiveMinutes: -300
        case .threeMinutes: -180
        case .oneMinute: -60
        }
    }

    var titlePrefix: String {
        switch self {
        case .tenMinutes: "発売10分前"
        case .fiveMinutes: "まもなく発売"
        case .threeMinutes: "準備開始"
        case .oneMinute: "まもなく開始"
        }
    }
}

struct TicketNotificationPlan: Equatable, Sendable {
    let identifier: String
    let eventID: UUID
    let fireDate: Date
    let title: String
    let timing: TicketNotificationTiming

    static func pendingPlans(for event: TicketEvent, now: Date = Date()) -> [Self] {
        TicketNotificationTiming.allCases.compactMap { timing in
            let fireDate = event.saleDate.addingTimeInterval(timing.offset)
            guard fireDate > now else {
                return nil
            }
            return TicketNotificationPlan(
                identifier: identifier(eventID: event.id, timing: timing),
                eventID: event.id,
                fireDate: fireDate,
                title: "\(timing.titlePrefix)：\(event.name)",
                timing: timing
            )
        }
    }

    static func identifiers(eventID: UUID) -> [String] {
        TicketNotificationTiming.allCases.map {
            identifier(eventID: eventID, timing: $0)
        }
    }

    static func removableIdentifiers(eventID: UUID) -> [String] {
        identifiers(eventID: eventID) + [
            "ticket.\(eventID.uuidString).sale-time"
        ]
    }

    private static func identifier(eventID: UUID, timing: TicketNotificationTiming) -> String {
        "ticket.\(eventID.uuidString).\(timing.rawValue)"
    }
}
