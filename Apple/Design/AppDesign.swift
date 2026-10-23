import SwiftUI

enum AppPalette {
    static let ink = Color.primary
    static let canvas = adaptive(1, 0.055)
    static let surface = adaptive(0.965, 0.10)
    static let raised = adaptive(1, 0.13)
    static let muted = adaptive(0.40, 0.68)
    static let line = Color.primary.opacity(0.09)
    static func actionFill(_ scheme: ColorScheme) -> Color { scheme == .dark ? .white : .black }
    static func actionText(_ scheme: ColorScheme) -> Color { scheme == .dark ? .black : .white }
    static func adaptive(_ light: Double, _ dark: Double) -> Color {
        #if os(macOS)
        Color(nsColor: NSColor(name: nil) { appearance in
            NSColor(white: appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light, alpha: 1)
        })
        #elseif os(iOS)
        Color(uiColor: UIColor { UIColor(white: $0.userInterfaceStyle == .dark ? dark : light, alpha: 1) })
        #else
        Color(white: dark)
        #endif
    }
}

enum AppAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var title: String { switch self { case .system: "跟随系统"; case .light: "浅色"; case .dark: "深色" } }
    var scheme: ColorScheme? { switch self { case .system: nil; case .light: .light; case .dark: .dark } }
}

struct BrandMark: View {
    var size: CGFloat = 40
    var body: some View {
        Image("BrandMark").resizable().renderingMode(.template).scaledToFit()
            .frame(width: size, height: size).foregroundStyle(.primary)
            .accessibilityHidden(true)
    }
}

struct QuietPressStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.62 : 1)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.975 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: configuration.isPressed)
    }
}

/// Explicit label and fill colors avoid native prominent buttons resolving
/// semantic `.primary` to the same color for both in a monochrome theme.
struct FilledButtonStyle: ButtonStyle {
    @Environment(\.colorScheme) private var scheme
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.medium))
            .padding(.horizontal, 12).padding(.vertical, 6)
            #if os(iOS)
            .frame(minHeight: 44)
            #else
            .frame(minHeight: 28)
            #endif
            .foregroundStyle(AppPalette.actionText(scheme))
            .background(AppPalette.actionFill(scheme),
                        in: RoundedRectangle(cornerRadius: 6))
            .opacity(enabled ? (configuration.isPressed ? 0.75 : 1) : 0.35)
    }
}

struct SurfaceCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content.padding(24).frame(maxWidth: .infinity, alignment: .leading)
            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 24))
            .overlay { RoundedRectangle(cornerRadius: 24).strokeBorder(AppPalette.line, lineWidth: 0.7) }
    }
}

struct SectionHeading: View {
    let title: String
    var detail: String? = nil
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(LocalizedStringKey(title)).font(.headline)
            Spacer()
            if let detail { Text(LocalizedStringKey(detail)).font(.caption).foregroundStyle(AppPalette.muted) }
        }
    }
}

struct ProductTour: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme
    @State private var page = 0
    private let titles = ["先留住，\n再慢慢想。", "人在别处，\n声音也能到达。", "声音与文字，\n留在自己的文件夹。"]
    private let captions = [
        "手机和手表随手记录。先保存原音，每一段都可以回听。",
        "两端选择同一个云端资料夹。电脑离线时，手机继续保存；电脑上线后再接收。",
        "电脑配置语音服务后可转写原音，并保留可编辑的文字文件。接收成功不代表已经生成文字。"
    ]
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                HStack(spacing: 6) { BrandMark(size: 32); Text("声记").font(.headline) }
                Spacer()
                Button("稍后再看") { dismiss() }.font(.subheadline).foregroundStyle(AppPalette.muted)
            }.padding(24)
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    HStack(spacing: 6) {
                        ForEach(0..<3) { i in
                            Capsule().fill(i == page ? AppPalette.ink : AppPalette.line)
                                .frame(width: i == page ? 36 : 12, height: 3)
                        }
                    }.padding(.top, 24)
                    Text(LocalizedStringKey(titles[page])).font(.system(.largeTitle, weight: .semibold))
                        .tracking(-1).fixedSize(horizontal: false, vertical: true)
                    tourVisual.padding(.vertical, 24)
                    Text(LocalizedStringKey(captions[page])).font(.body).foregroundStyle(AppPalette.muted).lineSpacing(6)
                    if page == 1 {
                        Label("没有网络也能录音，恢复连接后继续", systemImage: "checkmark.shield")
                            .font(.footnote).foregroundStyle(AppPalette.muted)
                    }
                }.id(page).transition(.opacity.combined(with: .offset(y: reduceMotion ? 0 : 8)))
                    .padding(.horizontal, 28).padding(.bottom, 32)
            }
            HStack {
                if page > 0 {
                    Button("上一步") { advance(-1) }.foregroundStyle(AppPalette.muted).frame(minHeight: 48)
                }
                Spacer()
                Button(L(page == 2 ? "开始使用" : "继续")) {
                    if page == 2 { dismiss() } else { advance(1) }
                }.font(.body.weight(.semibold)).padding(.horizontal, 28).padding(.vertical, 16)
                    .foregroundStyle(AppPalette.actionText(scheme)).background(AppPalette.actionFill(scheme), in: Capsule())
                    .buttonStyle(QuietPressStyle())
            }.padding(24)
        }.background(AppPalette.canvas)
            .frame(idealWidth: 500, idealHeight: 650).appLanguage()
    }
    private var tourVisual: some View {
        HStack(spacing: 20) {
            if page == 0 {
                Spacer(); BrandMark(size: 132); Spacer()
            } else {
                Spacer()
                ForEach(Array((page == 1 ? ["iphone", "icloud", "laptopcomputer"] : ["waveform", "text.alignleft", "folder"]).enumerated()), id: \.offset) { i, symbol in
                    if i > 0 { Image(systemName: "arrow.right").font(.caption).foregroundStyle(.tertiary) }
                    Image(systemName: symbol).font(.system(size: 32, weight: .light))
                }
                Spacer()
            }
        }.frame(minHeight: 130).accessibilityElement(children: .ignore)
            .accessibilityLabel(page == 1 ? "手机经过云端资料夹同步到电脑" : titles[page])
    }
    private func advance(_ value: Int) {
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.28)) { page += value }
    }
}
