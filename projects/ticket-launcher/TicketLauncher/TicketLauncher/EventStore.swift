import Foundation
import Combine

@MainActor
final class EventStore: ObservableObject {
    @Published private(set) var events: [TicketEvent]
    private(set) var revision: UInt64 = 0

    private let defaults: UserDefaults
    private let storageKey: String
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(
        defaults: UserDefaults = .standard,
        storageKey: String = "ticketEvents.v1"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
        encoder = JSONEncoder()
        decoder = JSONDecoder()
        events = []
        events = loadEvents()
    }

    var upcomingEvents: [TicketEvent] {
        upcomingEvents(at: Date())
    }

    var pastEvents: [TicketEvent] {
        pastEvents(at: Date())
    }

    func upcomingEvents(at date: Date) -> [TicketEvent] {
        events
            .filter { $0.saleDate > date }
            .sorted { $0.saleDate < $1.saleDate }
    }

    func pastEvents(at date: Date) -> [TicketEvent] {
        events
            .filter { $0.saleDate <= date }
            .sorted { $0.saleDate > $1.saleDate }
    }

    func event(id: UUID) -> TicketEvent? {
        events.first { $0.id == id }
    }

    func save(_ event: TicketEvent) throws {
        if let index = events.firstIndex(where: { $0.id == event.id }) {
            events[index] = event
        } else {
            events.append(event)
        }
        try persist()
        revision &+= 1
    }

    func delete(_ event: TicketEvent) throws {
        events.removeAll { $0.id == event.id }
        try persist()
        revision &+= 1
    }

    private func loadEvents() -> [TicketEvent] {
        guard let data = defaults.data(forKey: storageKey) else {
            return []
        }
        return (try? decoder.decode([TicketEvent].self, from: data)) ?? []
    }

    private func persist() throws {
        defaults.set(try encoder.encode(events), forKey: storageKey)
    }
}
