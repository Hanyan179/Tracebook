import SwiftUI

struct RecordingRepairSection: View {
    @ObservedObject var sync: NodeSyncController
    @State private var selected: Recording?
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !sync.recordingsNeedingRepair.isEmpty {
                Text("接收文件需处理").font(.headline)
                ForEach(sync.recordingsNeedingRepair) { record in repairButton(record) }
            }
            if !sync.recentlyRepairedRecordings.isEmpty {
                DisclosureGroup("最近修复") {
                    ForEach(sync.recentlyRepairedRecordings) { record in repairButton(record) }
                }
            }
        }.sheet(item: $selected) { record in RecordingRepairSheet(sync: sync, recording: record) }
    }
    private func repairButton(_ record: Recording) -> some View {
        Button { selected = record } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "wrench.and.screwdriver")
                VStack(alignment: .leading, spacing: 5) {
                    Text(record.displayTitle).font(.subheadline.weight(.medium))
                    Text(record.startedAt, format: .dateTime.month().day().hour().minute()).font(.caption)
                    Text(L(sync.label(for: record.id))).font(.caption).foregroundStyle(.secondary)
                }.fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.caption)
            }.padding(.vertical, 8).frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityLabel(LF("核对原音修复：%@", record.displayTitle))
    }
}

private struct RecordingRepairSheet: View {
    @ObservedObject var sync: NodeSyncController
    let recording: Recording
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme
    @State private var plan: RecordingRepairPlan?
    @State private var result: RecordingRepairStatus?
    @State private var problem: String?
    @State private var submissionProblem: String?
    @State private var loading = false
    @State private var loadGeneration = UUID()
    private var job: TransferJob? { sync.job(for: recording.id) }
    private var hasPending: Bool { job?.repairRequestID != nil }
    private var refreshKey: String {
        (sync.verifiedReceiver?.id ?? "") + (job?.repairRequestID?.uuidString ?? job?.lastRepairRequestID?.uuidString ?? "") + (job?.state.rawValue ?? "")
    }
    var body: some View {
        NavigationStack {
            Form {
                Section("本机原音") {
                    Text(recording.displayTitle).font(.headline)
                    Text(recording.startedAt, format: .dateTime.year().month().day().hour().minute())
                    LabeledContent("文件大小", value: recording.byteCount.formatted(.byteCount(style: .file).locale(AppLanguage.current.locale)))
                }
                if hasPending {
                    Section("修复进度") {
                        if job?.state == .failed {
                            Label("修复等待重试", systemImage: "exclamationmark.circle")
                            if let message = job?.message { Text(L(message)).font(.callout) }
                            Button("继续同次修复") { sync.retryRepair(recording.id) }
                        } else if sync.verifiedReceiver == nil {
                            Label("等待电脑重新连接", systemImage: "network")
                            Button("继续同次修复") { sync.retryRepair(recording.id) }
                        } else {
                            ProgressView(L("正在修复接收文件"))
                        }
                        Text("可以返回，修复任务和本机原音都会保留。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } else if let result, result.state == "done", job?.state == .delivered {
                    Section("修复记录") {
                        Label("接收文件已核验完整", systemImage: "checkmark.circle")
                        if let completed = result.completedAt, let date = ISO8601DateFormatter().date(from: completed) {
                            Text(date, format: .dateTime.year().month().day().hour().minute())
                        }
                        LabeledContent("保留的接收文件", value: String(result.keptFiles))
                        if let archive = result.archivePath {
                            DisclosureGroup("旧副本位置") { Text(verbatim: archive).font(.caption).textSelection(.enabled) }
                        }
                        Text("保留的旧文件位于电脑资料库，并会纳入完整备份。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } else if let plan {
                    previewSections(plan)
                } else if loading {
                    Section { ProgressView(L("正在核对本机原音与接收文件")) }
                }
                if let submissionProblem {
                    Section { Text(L(submissionProblem)).fixedSize(horizontal: false, vertical: true) }
                }
                if let problem {
                    Section {
                        Text(L(problem)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        Button("重新核对") { Task { await load() } }.disabled(loading)
                    }
                }
            }.formStyle(.grouped)
                .navigationTitle(L("核对原音修复"))
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
                .task(id: refreshKey) { await load() }
                .onChange(of: job?.repairRequestID) { old, new in
                    if old != nil, new == nil, job?.state != .delivered { submissionProblem = job?.message }
                }
        }.appLanguage()
        #if os(macOS)
        .frame(minWidth: 440, idealWidth: 540, minHeight: 420, idealHeight: 650)
        #endif
    }
    @ViewBuilder private func previewSections(_ plan: RecordingRepairPlan) -> some View {
        Section("电脑接收文件") {
            LabeledContent("原音文件", value: L(stateTitle(plan.preview.audioState)))
            LabeledContent("接收信息", value: L(stateTitle(plan.preview.manifestState)))
            if plan.preview.canRepair {
                Text("本机原音及重点标记已匹配接收登记。")
                    .font(.footnote).foregroundStyle(.secondary)
            } else { Label("接收文件已完整，无需修复。", systemImage: "checkmark.circle") }
        }
        if plan.preview.canRepair {
            Section("修复范围") {
                Text("保留电脑现有副本后，用本机原音恢复这条录音及接收信息。文字、记忆和历史继续保留。")
                    .fixedSize(horizontal: false, vertical: true)
                LabeledContent("将保留的旧文件", value: String(plan.preview.keptFiles))
                Button {
                    do { try sync.beginRepair(plan); self.plan = nil; problem = nil; submissionProblem = nil }
                    catch { problem = error.localizedDescription }
                } label: {
                    Text(L(plan.preview.pendingRequestID == nil ? "保留旧副本并修复" : "继续同次修复"))
                        .padding(.horizontal, 16).padding(.vertical, 10)
                        .frame(maxWidth: .infinity, minHeight: 44).fixedSize(horizontal: false, vertical: true)
                        .foregroundStyle(AppPalette.actionText(scheme))
                        .background(AppPalette.actionFill(scheme), in: RoundedRectangle(cornerRadius: 14))
                }.buttonStyle(.plain).disabled(loading || sync.verifiedReceiver == nil)
                    .accessibilityIdentifier("recording.repair.confirm")
            }
        } else {
            Section { Button("更新接收状态") { sync.retry() } }
        }
    }
    private func stateTitle(_ value: String) -> String {
        switch value { case "matching": "与接收登记一致"; case "missing": "文件缺失"; default: "内容已变化" }
    }
    private func load() async {
        let generation = UUID(); loadGeneration = generation
        loading = true; problem = nil
        defer { if loadGeneration == generation { loading = false } }
        do {
            if let key = job?.repairRequestID ?? (job?.state == .delivered ? job?.lastRepairRequestID : nil) {
                plan = nil
                do {
                    let response = try await sync.repairStatus(recording.id, requestID: key)
                    try Task.checkCancellation()
                    guard loadGeneration == generation else { return }
                    result = response
                } catch let failure as NodeFailure where hasPending && failure.statusCode == 404 {
                    guard !Task.isCancelled, loadGeneration == generation else { return }
                    result = nil
                }
            } else {
                let response = try await sync.previewRepair(recording.id)
                try Task.checkCancellation()
                guard loadGeneration == generation else { return }
                result = nil; plan = response
            }
        } catch is CancellationError { }
        catch {
            if !Task.isCancelled, loadGeneration == generation { problem = error.localizedDescription }
        }
    }
}
