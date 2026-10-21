import Foundation

extension Recording {
    var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        return LF("%@ 的录音", startedAt.formatted(.dateTime.hour().minute().locale(AppLanguage.current.locale)))
    }
    var statusLabel: String {
        switch status {
        case .recording: L("正在录音")
        case .paused: L("已暂停")
        case .saved: L("已保存")
        case .recovered: L("已恢复")
        case .interrupted: L("录音中断")
        case .failed: L("未完成")
        }
    }
    var sourceLabel: String {
        L(sourceTitle)
    }
    private var sourceTitle: String {
        if trigger == "import" { return "文件导入" }
        switch sourceDevice {
        case .phone: return trigger == "shortcut" ? "手机 · 快捷指令" : "手机录音"
        case .watch: return trigger == "shortcut" ? "手表 · 快捷指令" : "手表录音"
        case nil: return trigger == "shortcut" ? "快捷指令" : "设备录音"
        }
    }
}
