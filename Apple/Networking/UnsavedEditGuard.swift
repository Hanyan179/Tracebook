import SwiftUI

private struct UnsavedEditGuard: ViewModifier {
    @ObservedObject var workspace: NodeWorkspaceStore
    let editing: Bool
    @State private var identifier = UUID()
    func body(content: Content) -> some View {
        content
            .onAppear { workspace.trackEditor(identifier, unfinished: editing) }
            .onChange(of: editing) { _, value in workspace.trackEditor(identifier, unfinished: value) }
            .onDisappear { workspace.trackEditor(identifier, unfinished: false) }
    }
}

extension View {
    func protectsUnsavedEdits(in workspace: NodeWorkspaceStore, editing: Bool) -> some View {
        modifier(UnsavedEditGuard(workspace: workspace, editing: editing))
    }
}
