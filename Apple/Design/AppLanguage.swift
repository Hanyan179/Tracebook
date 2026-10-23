import SwiftUI

enum AppLanguage: String, CaseIterable, Identifiable {
    case system, chinese, english
    var id: String { rawValue }
    var title: String {
        switch self { case .system: "跟随系统"; case .chinese: "简体中文"; case .english: "English" }
    }
    var identifier: String {
        switch self {
        case .chinese: "zh-Hans"
        case .english: "en"
        case .system: Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "zh-Hans" : "en"
        }
    }
    var locale: Locale { Locale(identifier: identifier) }
    static var current: AppLanguage {
        AppLanguage(rawValue: UserDefaults.standard.string(forKey: "app.language") ?? "system") ?? .system
    }
}

/// Use for messages and variable UI labels; user-authored content stays verbatim.
func L(_ key: String) -> String {
    guard let path = Bundle.main.path(forResource: AppLanguage.current.identifier, ofType: "lproj"),
          let bundle = Bundle(path: path) else { return key }
    return bundle.localizedString(forKey: key, value: key, table: nil)
}

func LF(_ key: String, _ values: CVarArg...) -> String {
    let singular = ["处理 %d 条同步问题": "Resolve %d sync issue", "%d 条原音已接收": "%d recording received", "找到 %d 条相关来源": "Found %d relevant source", "%d 条资料": "%d item", "%d 条来源": "%d source", "%d 条记忆": "%d memory", "%d 段原音": "%d recording"]
    let format = AppLanguage.current.identifier == "en" && (values.first as? Int) == 1
        ? (singular[key] ?? L(key)) : L(key)
    return String(format: format, locale: AppLanguage.current.locale, arguments: values)
}

private struct AppLanguageEnvironment: ViewModifier {
    @AppStorage("app.language") private var language = AppLanguage.system.rawValue
    func body(content: Content) -> some View {
        content.environment(\.locale, (AppLanguage(rawValue: language) ?? .system).locale)
    }
}

extension View {
    func appLanguage() -> some View { modifier(AppLanguageEnvironment()) }
}
