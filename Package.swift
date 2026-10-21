// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "VoiceMemoryCore",
    platforms: [.macOS(.v14), .iOS(.v18), .watchOS(.v11)],
    products: [.library(name: "VoiceMemoryCore", targets: ["VoiceMemoryCore"])],
    targets: [
        .target(name: "VoiceMemoryCore"),
        .testTarget(name: "VoiceMemoryCoreTests", dependencies: ["VoiceMemoryCore"])
    ]
)
