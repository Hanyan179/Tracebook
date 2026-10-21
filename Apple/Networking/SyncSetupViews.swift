import SwiftUI
import UniformTypeIdentifiers

struct FolderSetupCard: View {
    @ObservedObject var folder: FolderSyncController
    var isDesktop = false
    @State private var choosing = false
    @State private var details = false
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.colorScheme) private var scheme
    var body: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 22) {
                HStack {
                    Image(systemName: "folder").font(.system(size: 26, weight: .light))
                    Spacer()
                    if folder.busy { ProgressView().controlSize(.small) }
                    else if folder.problem != nil { Image(systemName: "exclamationmark.circle").foregroundStyle(.orange) }
                    else if folder.configured { Text(L(folder.statusTitle)).font(.caption).foregroundStyle(AppPalette.muted) }
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text(folder.configured ? folder.folderName : L("选择同一个资料夹"))
                        .font(.title3.weight(.semibold))
                    Text(L(isDesktop ? "接收手机送来的原音，在这台电脑整理。" : "把录音放入所选资料夹，手机原音继续保留。"))
                        .font(.subheadline).foregroundStyle(AppPalette.muted)
                }
                Button(L(folder.configured ? "更换资料夹" : "选择资料夹")) { choosing = true }
                    .font(.subheadline.weight(.semibold)).padding(.vertical, 12).padding(.horizontal, 20)
                    .foregroundStyle(AppPalette.actionText(scheme)).background(AppPalette.actionFill(scheme), in: Capsule())
                    .buttonStyle(QuietPressStyle()).disabled(folder.busy)
                if !isDesktop && !folder.configured {
                    Text("选择后，已有和后续录音会交给这个资料夹；手机原音继续保留。")
                        .font(.caption).foregroundStyle(AppPalette.muted)
                }
                if folder.configured {
                    let layout = typeSize.isAccessibilitySize
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
                        : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                    layout {
                        Label("\(folder.delivered) 已接收", systemImage: "checkmark")
                        if folder.waiting > 0 { Label("\(folder.waiting) 等待中", systemImage: "clock") }
                        if !typeSize.isAccessibilitySize { Spacer() }
                        Button { Task { await folder.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                            .disabled(folder.busy).accessibilityLabel("检查资料夹同步")
                    }.font(.caption).foregroundStyle(AppPalette.muted)
                    if let date = folder.lastChecked {
                        Text("上次检查 \(date.formatted(.dateTime.hour().minute()))").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                if let problem = folder.problem {
                    Label(problem, systemImage: "exclamationmark.circle")
                        .font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                }
                DisclosureGroup("连接指南", isExpanded: $details) {
                    VStack(alignment: .leading, spacing: 16) {
                        guideRow("1", "在 iCloud Drive（苹果云盘）里建一个“声记”文件夹。")
                        guideRow("2", "手机和电脑都选择这个文件夹，并使用同一个云盘账号。")
                        guideRow("3", "录音后检查接收状态。电脑离线时，原音仍保存在手机。")
                        Text("选择“我的 iPhone”或普通本地目录不会自动跨设备；其他云盘需要两端都已开启同步。")
                            .font(.caption).foregroundStyle(AppPalette.muted)
                    }.padding(.top, 16)
                }.font(.subheadline).tint(.primary)
            }
        }
        .fileImporter(isPresented: $choosing, allowedContentTypes: [.folder]) { result in
            do { try folder.select(result.get()) }
            catch { folder.problem = error.localizedDescription }
        }
    }
    private func guideRow(_ number: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number).font(.caption.monospacedDigit()).frame(width: 22, height: 22)
                .background(AppPalette.line, in: Circle())
            Text(LocalizedStringKey(text)).font(.footnote).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct SyncJourney: View {
    var route: SyncRoute = .folder
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: 16)) : AnyLayout(HStackLayout(spacing: 0))
        layout {
            stage("iphone", "手机", "先保存原音")
            if route != .local {
                arrow
                if route == .folder {
                    stage("folder", "共享资料夹", "两端选择同一位置")
                    arrow
                }
                stage("laptopcomputer", "电脑", route == .direct ? "同一网络下接收" : "接收与整理")
            }
        }.padding(.vertical, 24)
    }
    private var arrow: some View {
        Image(systemName: typeSize.isAccessibilitySize ? "arrow.down" : "arrow.right")
            .font(.caption).foregroundStyle(.tertiary).accessibilityHidden(true)
    }
    private func stage(_ symbol: String, _ title: String, _ subtitle: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: symbol).font(.system(size: 24, weight: .light)).frame(height: 30)
            Text(LocalizedStringKey(title)).font(.caption.weight(.medium))
            Text(LocalizedStringKey(subtitle)).font(.caption2).foregroundStyle(AppPalette.muted).multilineTextAlignment(.center)
        }.frame(maxWidth: .infinity)
    }
}
