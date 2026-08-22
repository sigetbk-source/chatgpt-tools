import SwiftUI
import UIKit

enum TicketCountdownDecision: Equatable, Sendable {
    case waiting(TimeInterval)
    case openSafari
    case manualOpenRequired
    case completed
}

enum TicketCountdownPolicy {
    static func decision(
        saleDate: Date,
        now: Date,
        isSceneActive: Bool,
        wasInterrupted: Bool,
        hasOpenedSafari: Bool
    ) -> TicketCountdownDecision {
        if hasOpenedSafari {
            return .completed
        }
        if wasInterrupted || !isSceneActive {
            return .manualOpenRequired
        }
        let remaining = saleDate.timeIntervalSince(now)
        return remaining > 0 ? .waiting(remaining) : .openSafari
    }
}

struct IdleTimerLease {
    private var previousValue: Bool?

    mutating func acquire(currentValue: Bool) -> Bool? {
        guard previousValue == nil else { return nil }
        previousValue = currentValue
        return true
    }

    mutating func release() -> Bool? {
        guard let previousValue else { return nil }
        self.previousValue = nil
        return previousValue
    }
}

struct TicketCountdownView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase

    let event: TicketEvent
    let openSafari: (URL) -> Void

    @State private var remaining: TimeInterval
    @State private var wasInterrupted = false
    @State private var hasOpenedSafari = false
    @State private var idleTimerLease = IdleTimerLease()

    init(event: TicketEvent, openSafari: @escaping (URL) -> Void) {
        self.event = event
        self.openSafari = openSafari
        _remaining = State(initialValue: max(0, event.saleDate.timeIntervalSinceNow))
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Spacer()

                Image(systemName: wasInterrupted ? "exclamationmark.triangle.fill" : "timer")
                    .font(.system(size: 52))
                    .foregroundStyle(wasInterrupted ? .orange : .blue)

                VStack(spacing: 8) {
                    Text(event.name)
                        .font(.title2.bold())
                        .multilineTextAlignment(.center)

                    if wasInterrupted {
                        Text("待機が中断されました")
                            .font(.title3.bold())
                        Text("画面ロックまたはアプリ切り替えを検出したため、自動起動は行いません。")
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    } else if hasOpenedSafari {
                        Text("Safariを開きました")
                            .font(.title3.bold())
                    } else {
                        Text(countdownText)
                            .font(.system(size: 56, weight: .bold, design: .monospaced))
                            .contentTransition(.numericText(countsDown: true))
                            .accessibilityLabel("発売まで\(accessibilityCountdownText)")
                        Text("発売時刻にSafariを自動で開きます")
                            .foregroundStyle(.secondary)
                    }
                }

                if wasInterrupted {
                    Button("販売ページを今すぐ開く", systemImage: "safari") {
                        hasOpenedSafari = true
                        restoreIdleTimer()
                        openSafari(event.saleURL)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                } else if !hasOpenedSafari {
                    Label("この画面を表示したまま、iPhoneをロックせずにお待ちください", systemImage: "lock.open")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }

                Spacer()
            }
            .padding(24)
            .navigationTitle("発売待機")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("閉じる") { dismiss() }
                }
            }
            .interactiveDismissDisabled(!wasInterrupted && !hasOpenedSafari)
            .onAppear(perform: disableIdleTimer)
            .onDisappear(perform: restoreIdleTimer)
            .onChange(of: scenePhase) {
                guard
                    scenePhase != .active,
                    !hasOpenedSafari
                else {
                    return
                }
                wasInterrupted = true
                restoreIdleTimer()
            }
            .task {
                await runCountdown()
            }
        }
    }

    private var countdownText: String {
        let tenths = Int(ceil(max(0, remaining) * 10))
        let minutes = tenths / 600
        let seconds = (tenths / 10) % 60
        let fraction = tenths % 10
        return String(format: "%02d:%02d.%d", minutes, seconds, fraction)
    }

    private var accessibilityCountdownText: String {
        let seconds = Int(ceil(max(0, remaining)))
        return "\(seconds / 60)分\(seconds % 60)秒"
    }

    @MainActor
    private func runCountdown() async {
        while !Task.isCancelled {
            switch TicketCountdownPolicy.decision(
                saleDate: event.saleDate,
                now: Date(),
                isSceneActive: scenePhase == .active,
                wasInterrupted: wasInterrupted,
                hasOpenedSafari: hasOpenedSafari
            ) {
            case .waiting(let interval):
                remaining = interval
                let updateInterval = interval > 10 ? 0.5 : 0.05
                try? await Task.sleep(for: .seconds(updateInterval))
            case .openSafari:
                remaining = 0
                hasOpenedSafari = true
                restoreIdleTimer()
                openSafari(event.saleURL)
                return
            case .manualOpenRequired, .completed:
                return
            }
        }
    }

    @MainActor
    private func disableIdleTimer() {
        guard let disabled = idleTimerLease.acquire(
            currentValue: UIApplication.shared.isIdleTimerDisabled
        ) else {
            return
        }
        UIApplication.shared.isIdleTimerDisabled = disabled
    }

    @MainActor
    private func restoreIdleTimer() {
        guard let previousValue = idleTimerLease.release() else { return }
        UIApplication.shared.isIdleTimerDisabled = previousValue
    }
}
