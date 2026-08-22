import Foundation

struct TicketEvent: Codable, Equatable, Identifiable, Sendable {
    let id: UUID
    var name: String
    var saleDate: Date
    var saleURL: URL
    var memo: String
    let createdAt: Date
    var updatedAt: Date

    init(
        id: UUID = UUID(),
        name: String,
        saleDate: Date,
        saleURL: URL,
        memo: String = "",
        createdAt: Date = Date(),
        updatedAt: Date = Date()
    ) {
        self.id = id
        self.name = name
        self.saleDate = saleDate
        self.saleURL = saleURL
        self.memo = memo
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

struct TicketEventInput: Equatable, Sendable {
    var name: String
    var saleDate: Date
    var saleURLText: String
    var memo: String
}

enum TicketEventValidationError: LocalizedError, Equatable {
    case emptyName
    case invalidURL
    case pastSaleDate

    var errorDescription: String? {
        switch self {
        case .emptyName:
            "イベント名を入力してください。"
        case .invalidURL:
            "https:// から始まる販売URLを入力してください。"
        case .pastSaleDate:
            "これから発売される日時を指定してください。"
        }
    }
}

enum TicketEventValidator {
    static func validatedEvent(
        from input: TicketEventInput,
        existing: TicketEvent? = nil,
        now: Date = Date()
    ) throws -> TicketEvent {
        let name = input.name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else {
            throw TicketEventValidationError.emptyName
        }

        guard
            let components = URLComponents(string: input.saleURLText.trimmingCharacters(in: .whitespacesAndNewlines)),
            components.scheme?.lowercased() == "https",
            components.host?.isEmpty == false,
            let saleURL = components.url
        else {
            throw TicketEventValidationError.invalidURL
        }

        if input.saleDate <= now {
            let isUnchangedPastEvent = existing.map {
                $0.saleDate <= now && $0.saleDate == input.saleDate
            } ?? false
            guard isUnchangedPastEvent else {
                throw TicketEventValidationError.pastSaleDate
            }
        }

        let timestamp = now
        return TicketEvent(
            id: existing?.id ?? UUID(),
            name: name,
            saleDate: input.saleDate,
            saleURL: saleURL,
            memo: input.memo.trimmingCharacters(in: .whitespacesAndNewlines),
            createdAt: existing?.createdAt ?? timestamp,
            updatedAt: timestamp
        )
    }
}
