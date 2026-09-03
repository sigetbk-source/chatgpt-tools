import SwiftUI
import UIKit

enum TicketDateSelection {
    static let minuteInterval = 5

    static func initialEditorDate(
        existingDate: Date?,
        now: Date = Date(),
        calendar: Calendar = .current
    ) -> Date {
        if let existingDate, existingDate <= now {
            return existingDate
        }

        return roundedUp(
            existingDate ?? now.addingTimeInterval(3_600),
            calendar: calendar
        )
    }

    static func roundedUp(
        _ date: Date,
        calendar: Calendar = .current
    ) -> Date {
        guard let startOfMinute = calendar.dateInterval(of: .minute, for: date)?.start else {
            return date
        }

        let minute = calendar.component(.minute, from: startOfMinute)
        let remainder = minute % minuteInterval
        let hasSubminuteValue = date > startOfMinute
        let minutesToAdd: Int

        if remainder == 0, !hasSubminuteValue {
            minutesToAdd = 0
        } else if remainder == 0 {
            minutesToAdd = minuteInterval
        } else {
            minutesToAdd = minuteInterval - remainder
        }

        return calendar.date(
            byAdding: .minute,
            value: minutesToAdd,
            to: startOfMinute
        ) ?? date
    }

    static func normalizedPickerValue(
        _ date: Date,
        calendar: Calendar = .current
    ) -> Date {
        guard let startOfMinute = calendar.dateInterval(of: .minute, for: date)?.start else {
            return date
        }

        let minute = calendar.component(.minute, from: startOfMinute)
        return calendar.date(
            byAdding: .minute,
            value: -(minute % minuteInterval),
            to: startOfMinute
        ) ?? date
    }
}

struct FiveMinuteDatePicker: UIViewRepresentable {
    @Binding var selection: Date

    func makeCoordinator() -> Coordinator {
        Coordinator(selection: $selection)
    }

    func makeUIView(context: Context) -> UIDatePicker {
        let picker = UIDatePicker()
        picker.datePickerMode = .dateAndTime
        picker.preferredDatePickerStyle = .compact
        picker.minuteInterval = TicketDateSelection.minuteInterval
        picker.locale = Locale(identifier: "ja_JP")
        picker.addTarget(
            context.coordinator,
            action: #selector(Coordinator.valueChanged(_:)),
            for: .valueChanged
        )
        picker.setContentHuggingPriority(.required, for: .horizontal)
        return picker
    }

    func updateUIView(_ picker: UIDatePicker, context: Context) {
        context.coordinator.selection = $selection
        picker.locale = Locale(identifier: "ja_JP")
        picker.minuteInterval = TicketDateSelection.minuteInterval

        if abs(picker.date.timeIntervalSince(selection)) >= 0.5 {
            picker.setDate(selection, animated: false)
        }
    }

    @MainActor
    final class Coordinator: NSObject {
        var selection: Binding<Date>

        init(selection: Binding<Date>) {
            self.selection = selection
        }

        @objc func valueChanged(_ sender: UIDatePicker) {
            selection.wrappedValue = TicketDateSelection.normalizedPickerValue(sender.date)
        }
    }
}
