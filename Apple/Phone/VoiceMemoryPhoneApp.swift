import SwiftUI

@main
struct VoiceMemoryPhoneApp: App {
    @UIApplicationDelegateAdaptor(PhoneAppDelegate.self) private var appDelegate
    var body: some Scene {
        WindowGroup { SetupView() }
    }
}
