import SwiftUI

struct RecordingMarkersView: View {
    let markers: [RecordingMarker]
    let duration: TimeInterval
    var disabled = false
    let select: (RecordingMarker) -> Void
    #if os(watchOS)
    @State private var expanded = false
    #else
    @State private var expanded = true
    #endif
    var body: some View {
        if !markers.isEmpty {
            #if os(watchOS)
            VStack(alignment: .leading, spacing: 8) {
                Button { expanded.toggle() } label: {
                    Label(LF("重点标记 %d", markers.count), systemImage: expanded ? "chevron.down" : "chevron.right")
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                }.accessibilityValue(L(expanded ? "已展开" : "已收起"))
                if expanded { markerRows }
            }
            #else
            DisclosureGroup(isExpanded: $expanded) {
                #if os(macOS)
                ScrollView { markerRows }.frame(maxHeight: 180)
                #else
                markerRows
                #endif
            } label: { Text(LF("重点标记 %d", markers.count)).font(.headline) }
            #endif
        }
    }
    private var markerRows: some View {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(markers.enumerated()), id: \.element.id) { index, marker in
                        VStack(alignment: .leading, spacing: 4) {
                            Button { select(marker) } label: {
                                Label(LF("重点 %d · %@", index + 1, marker.timeLabel), systemImage: "flag")
                                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                    .fixedSize(horizontal: false, vertical: true)
                            }.disabled(disabled || marker.offset >= duration)
                                .accessibilityHint(L("从标记位置回听原音"))
                            if marker.offset >= duration {
                                Text("这个标记超出已保存的原音，标记位置仍保留。")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }.padding(.top, 8)
    }
}

extension RecordingMarker {
    var timeLabel: String {
        let seconds = offsetMilliseconds / 1000
        return seconds >= 3600
            ? String(format: "%lld:%02lld:%02lld", seconds / 3600, seconds / 60 % 60, seconds % 60)
            : String(format: "%02lld:%02lld", seconds / 60, seconds % 60)
    }
}
