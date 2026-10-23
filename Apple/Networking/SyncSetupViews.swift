import SwiftUI
import UniformTypeIdentifiers

struct FolderSetupCard: View {
    @ObservedObject var folder: FolderSyncController
    var isDesktop = false
    @State private var choosing = false
    @State private var details = false
    @State private var failures = false
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
                        : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
                    layout {
                        Label(LF("%d 条原音已接收", folder.delivered), systemImage: "checkmark")
                        if folder.waiting > 0 { Label(LF("%d 条等待接收", folder.waiting), systemImage: "clock") }
                        if !typeSize.isAccessibilitySize { Spacer() }
                        Button { Task { await folder.refresh() } } label: {
                            Image(systemName: "arrow.clockwise").frame(minWidth: 44, minHeight: 44)
                        }
                            .disabled(folder.busy).accessibilityLabel("检查资料夹同步")
                    }.font(.caption).foregroundStyle(AppPalette.muted)
                    Text("当前资料夹的传输记录；已接收不代表已经转写。")
                        .font(.caption).foregroundStyle(AppPalette.muted)
                    if let date = folder.lastChecked {
                        Text("上次检查 \(date.formatted(.dateTime.hour().minute()))").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                if let problem = folder.problem {
                    Label(L(problem), systemImage: "exclamationmark.circle")
                        .font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if !folder.failedJobs.isEmpty {
                    Button(LF("处理 %d 条同步问题", folder.failedJobs.count)) { failures = true }
                        .font(.subheadline.weight(.medium)).frame(minHeight: 44)
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
        .sheet(isPresented: $failures) { FolderFailuresView(folder: folder).appLanguage() }
    }
    private func guideRow(_ number: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number).font(.caption.monospacedDigit()).frame(width: 22, height: 22)
                .background(AppPalette.line, in: Circle())
            Text(LocalizedStringKey(text)).font(.footnote).fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct FolderFailuresView: View {
    @ObservedObject var folder: FolderSyncController
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                if folder.failedJobs.isEmpty {
                    Label(L("目前没有待处理的同步问题"), systemImage: "checkmark.circle")
                    if folder.waiting > 0 {
                        Text(L("重新发送后仍需等待电脑接收回执。")).font(.footnote).foregroundStyle(.secondary)
                    }
                }
                ForEach(folder.failedJobs) { job in
                    NavigationLink(value: job.id) {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(folder.title(for: job.recordingID)).font(.headline)
                            Text(L(job.message ?? "需要处理")).font(.footnote).foregroundStyle(.secondary)
                        }.padding(.vertical, 6)
                    }
                }
            }.navigationTitle(L("同步问题"))
                .navigationDestination(for: String.self) { id in
                    if let job = folder.jobs.first(where: { $0.id == id }) {
                        FolderFailureDetail(folder: folder, initial: job, title: folder.title(for: job.recordingID))
                    }
                }
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button(L("关闭")) { dismiss() } } }
        }
        #if os(macOS)
        .frame(minWidth: 440, idealWidth: 520, minHeight: 400, idealHeight: 520)
        #endif
    }
}

private struct FolderFailureDetail: View {
    @ObservedObject var folder: FolderSyncController
    let initial: TransferJob
    let title: String
    @State private var working = false
    @State private var issue: String?
    @State private var recovered = false
    #if os(iOS)
    @State private var plan: FolderSyncController.RecoveryPlan?
    #endif
    private var current: TransferJob { folder.jobs.first { $0.id == initial.id } ?? initial }
    var body: some View {
        Form {
            Section {
                Text(title).font(.headline).fixedSize(horizontal: false, vertical: true)
                if current.state == .delivered { Label(L("电脑已接收"), systemImage: "checkmark.circle") }
                else if current.state == .awaitingReceipt { Label(L("等待电脑接收"), systemImage: "clock") }
                else { Text(L(current.message ?? "需要处理")).foregroundStyle(.secondary) }
            }
            if let issue { Section { Text(L(issue)).foregroundStyle(.secondary) } }
            if recovered {
                Section {
                    Label(L("旧副本已保留，原音已重新发送"), systemImage: "checkmark.circle")
                    Text(L("可在所选资料夹的 VoiceMemory/recovery 中找到旧副本。手机原音仍保留。"))
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } else if current.state == .failed {
                Section {
                    Button(L("重新检查")) { Task { await folder.refresh() } }
                    #if os(iOS)
                    if plan == nil {
                        Button(L("查看恢复方案")) {
                            Task {
                                working = true; issue = nil
                                defer { working = false }
                                do { plan = try await folder.previewRecovery(current) }
                                catch { issue = error.localizedDescription }
                            }
                        }
                    }
                    #else
                    Text(L("请在保存原音的手机上打开“同步问题”，核对后重新发送。电脑已有的原音不会被覆盖。"))
                        .font(.footnote).foregroundStyle(.secondary)
                    #endif
                }
                #if os(iOS)
                if let plan {
                    Section(L("恢复方案")) {
                        Text(LF("将使用手机原音重新发送到“%@”。", plan.folderName))
                        Text(L("当前交换副本将完整保留到该资料夹的 VoiceMemory/recovery，不删除任何原音。"))
                            .font(.footnote).foregroundStyle(.secondary)
                        Text(L("如果已有回执或内容发生变化，本次操作会停止。"))
                            .font(.footnote).foregroundStyle(.secondary)
                        Button(L("保留副本并重新发送")) {
                            Task {
                                working = true; issue = nil
                                defer { working = false }
                                do { try await folder.recover(plan); recovered = true; self.plan = nil }
                                catch { issue = error.localizedDescription; self.plan = nil }
                            }
                        }.fontWeight(.semibold)
                    }
                }
                #endif
            }
            if working { ProgressView() }
        }
        .formStyle(.grouped).navigationTitle(L("处理同步问题"))
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .disabled(working || folder.busy).interactiveDismissDisabled(working)
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
