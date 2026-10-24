#!/usr/bin/env python3
"""Generate the small Xcode project using only Python's standard library."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
objects = {}


def ident(value):
    return hashlib.sha1(value.encode()).hexdigest()[:24].upper()


def add(key, isa, **fields):
    uid = ident(key)
    objects[uid] = {"isa": isa, **fields}
    return uid


def source(path):
    return add(path, "PBXFileReference", lastKnownFileType="sourcecode.swift",
               path=path, sourceTree="SOURCE_ROOT")


config = add("config", "PBXFileReference", lastKnownFileType="text.xcconfig",
             path="Config/Project.xcconfig", sourceTree="SOURCE_ROOT")
ui_languages = [
    add(f"ui-{language}", "PBXFileReference", lastKnownFileType="text.plist.strings",
        name=language, path=f"Apple/Resources/{language}.lproj/Localizable.strings", sourceTree="SOURCE_ROOT")
    for language in ["zh-Hans", "en"]
]
ui_resource = add("ui-localizations", "PBXVariantGroup", children=ui_languages,
                  name="Localizable.strings", sourceTree="<group>")
info_languages = [
    add(f"info-{language}", "PBXFileReference", lastKnownFileType="text.plist.strings",
        name=language, path=f"Apple/Resources/{language}.lproj/InfoPlist.strings", sourceTree="SOURCE_ROOT")
    for language in ["zh-Hans", "en"]
]
info_resource = add("info-localizations", "PBXVariantGroup", children=info_languages,
                    name="InfoPlist.strings", sourceTree="<group>")
shortcut_languages = [
    add(f"shortcuts-{language}", "PBXFileReference", lastKnownFileType="text.plist.strings",
        name=language, path=f"Apple/Resources/{language}.lproj/AppShortcuts.strings", sourceTree="SOURCE_ROOT")
    for language in ["zh-Hans", "en"]
]
shortcut_resource = add("shortcut-localizations", "PBXVariantGroup", children=shortcut_languages,
                        name="AppShortcuts.strings", sourceTree="<group>")
watch_shortcut_languages = [
    add(f"watch-shortcuts-{language}", "PBXFileReference", lastKnownFileType="text.plist.strings",
        name=language, path=f"Apple/Resources/Watch/{language}.lproj/AppShortcuts.strings", sourceTree="SOURCE_ROOT")
    for language in ["zh-Hans", "en"]
]
watch_shortcut_resource = add("watch-shortcut-localizations", "PBXVariantGroup",
                              children=watch_shortcut_languages, name="AppShortcuts.strings", sourceTree="<group>")
shared = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Apple/Shared").glob("*.swift"))
design = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Apple/Design").glob("*.swift"))
shared += design
shared += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Sources/VoiceMemoryCore").glob("*.swift"))
watch = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Apple/Watch").glob("*.swift"))
watch += shared
phone = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Apple/Phone").glob("*.swift")) + shared
networking = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Apple/Networking").glob("*.swift"))
phone += networking
mac = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Apple/Mac").glob("*.swift")) + networking
mac += sorted(str(p.relative_to(ROOT)) for p in (ROOT / "Sources/VoiceMemoryCore").glob("*.swift"))
mac += ["Apple/Shared/AudioPlayback.swift"]
mac += design
refs = {path: source(path) for path in sorted(set(watch + phone + mac))}
pdf_worker = "Apple/PDFWorker/main.swift"
refs[pdf_worker] = source(pdf_worker)
brand_resource = add("brand-assets", "PBXFileReference", lastKnownFileType="folder.assetcatalog",
                     path="Apple/Resources/Brand.xcassets", sourceTree="SOURCE_ROOT")
icon_resources = {
    sdk: add(sdk + "-icons", "PBXFileReference", lastKnownFileType="folder.assetcatalog",
             path=f"Apple/Resources/{name}Icons.xcassets", sourceTree="SOURCE_ROOT")
    for sdk, name in [("iphoneos", "Phone"), ("macosx", "Mac"), ("watchos", "Watch")]
}
node_resources = [
    add(path, "PBXFileReference", lastKnownFileType="text.script.python", path=path, sourceTree="SOURCE_ROOT")
    for path in sorted(str(path.relative_to(ROOT)) for path in (ROOT / "node").glob("*.py"))
]
node_resources.append(add("python-runtime", "PBXFileReference", lastKnownFileType="folder",
    path="DerivedData/PythonRuntime", sourceTree="SOURCE_ROOT"))
node_resources.append(add("integration-skill", "PBXFileReference", lastKnownFileType="folder",
    path="integrations/lifto-library", sourceTree="SOURCE_ROOT"))
products = {}
targets = {}

for name, files, sdk, plist, bundle in [
    ("VoiceMemoryWatch", watch, "watchos", "Config/Watch-Info.plist", "$(VOICE_MEMORY_BUNDLE_PREFIX).watchkitapp"),
    ("VoiceMemory", phone, "iphoneos", "Config/Phone-Info.plist", "$(VOICE_MEMORY_BUNDLE_PREFIX)"),
    ("VoiceMemoryMac", mac, "macosx", "Config/Mac-Info.plist", "$(VOICE_MEMORY_BUNDLE_PREFIX).mac")
]:
    products[name] = add(name + ".app", "PBXFileReference", explicitFileType="wrapper.application",
                         includeInIndex="0", path=name + ".app", sourceTree="BUILT_PRODUCTS_DIR")
    sources = [add(name + path, "PBXBuildFile", fileRef=refs[path]) for path in files]
    if sdk == "macosx":
        resource_files = [add(name + resource, "PBXBuildFile", fileRef=resource) for resource in node_resources]
    else:
        resource_files = [add(name + "shortcutresource", "PBXBuildFile",
                              fileRef=watch_shortcut_resource if sdk == "watchos" else shortcut_resource)]
    resource_files.append(add(name + "brandresource", "PBXBuildFile", fileRef=brand_resource))
    resource_files.append(add(name + "uiresource", "PBXBuildFile", fileRef=ui_resource))
    resource_files.append(add(name + "inforesource", "PBXBuildFile", fileRef=info_resource))
    resource_files.append(add(name + "iconresource", "PBXBuildFile", fileRef=icon_resources[sdk]))
    phases = [
        add(name + "sources", "PBXSourcesBuildPhase", buildActionMask="2147483647",
            files=sources, runOnlyForDeploymentPostprocessing="0"),
        add(name + "frameworks", "PBXFrameworksBuildPhase", buildActionMask="2147483647",
            files=[], runOnlyForDeploymentPostprocessing="0"),
        add(name + "resources", "PBXResourcesBuildPhase", buildActionMask="2147483647",
            files=resource_files,
            runOnlyForDeploymentPostprocessing="0")
    ]
    configs = []
    for mode in ["Debug", "Release"]:
        settings = {
            "PRODUCT_NAME": name, "PRODUCT_BUNDLE_IDENTIFIER": bundle, "SDKROOT": sdk,
            "INFOPLIST_FILE": plist, "GENERATE_INFOPLIST_FILE": "NO",
            "TARGETED_DEVICE_FAMILY": "4" if sdk == "watchos" else ("1" if sdk == "iphoneos" else ""),
            "SUPPORTED_PLATFORMS": "macosx" if sdk == "macosx" else ("watchos watchsimulator" if sdk == "watchos" else "iphoneos iphonesimulator"),
            "MACOSX_DEPLOYMENT_TARGET": "14.0",
            "SWIFT_OPTIMIZATION_LEVEL": "-Onone" if mode == "Debug" else "-O",
            "SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG" if mode == "Debug" else "",
            "ENABLE_USER_SCRIPT_SANDBOXING": "YES",
            "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon",
            "CLANG_ENABLE_MODULES": "YES",
            "ENABLE_TESTABILITY": "YES" if mode == "Debug" else "NO",
            "DEBUG_INFORMATION_FORMAT": "dwarf" if mode == "Debug" else "dwarf-with-dsym",
            "SKIP_INSTALL": "YES" if sdk == "watchos" else "NO"
        }
        configs.append(add(name + mode, "XCBuildConfiguration", baseConfigurationReference=config,
                           buildSettings=settings, name=mode))
    configuration = add(name + "configs", "XCConfigurationList", buildConfigurations=configs,
                         defaultConfigurationIsVisible="0", defaultConfigurationName="Release")
    targets[name] = add(name, "PBXNativeTarget", buildConfigurationList=configuration,
                        buildPhases=phases, buildRules=[], dependencies=[], name=name,
                        productName=name, productReference=products[name],
                        productType="com.apple.product-type.application")

worker_name = "PDFTextWorker"
products[worker_name] = add(worker_name + "product", "PBXFileReference",
    explicitFileType="compiled.mach-o.executable", includeInIndex="0", path=worker_name, sourceTree="BUILT_PRODUCTS_DIR")
worker_configs = []
for mode in ["Debug", "Release"]:
    worker_configs.append(add(worker_name + mode, "XCBuildConfiguration", baseConfigurationReference=config,
        buildSettings={"PRODUCT_NAME": worker_name, "PRODUCT_BUNDLE_IDENTIFIER": "$(VOICE_MEMORY_BUNDLE_PREFIX).pdf-worker",
                       "SDKROOT": "macosx", "SUPPORTED_PLATFORMS": "macosx", "MACOSX_DEPLOYMENT_TARGET": "14.0",
                       "SWIFT_OPTIMIZATION_LEVEL": "-Onone" if mode == "Debug" else "-O",
                       "SKIP_INSTALL": "YES", "GENERATE_INFOPLIST_FILE": "NO", "CLANG_ENABLE_MODULES": "YES"}, name=mode))
worker_configuration = add(worker_name + "configs", "XCConfigurationList", buildConfigurations=worker_configs,
    defaultConfigurationIsVisible="0", defaultConfigurationName="Release")
targets[worker_name] = add(worker_name, "PBXNativeTarget", buildConfigurationList=worker_configuration,
    buildPhases=[add(worker_name + "sources", "PBXSourcesBuildPhase", buildActionMask="2147483647",
        files=[add(worker_name + "source", "PBXBuildFile", fileRef=refs[pdf_worker])], runOnlyForDeploymentPostprocessing="0"),
        add(worker_name + "frameworks", "PBXFrameworksBuildPhase", buildActionMask="2147483647", files=[], runOnlyForDeploymentPostprocessing="0")],
    buildRules=[], dependencies=[], name=worker_name, productName=worker_name, productReference=products[worker_name],
    productType="com.apple.product-type.tool")
project_id = ident("project")
worker_proxy = add("pdf-worker-proxy", "PBXContainerItemProxy", containerPortal=project_id, proxyType="1",
    remoteGlobalIDString=targets[worker_name], remoteInfo=worker_name)
objects[targets["VoiceMemoryMac"]]["dependencies"].append(add("pdf-worker-dependency", "PBXTargetDependency",
    target=targets[worker_name], targetProxy=worker_proxy))
worker_embed = add("pdf-worker-embed-file", "PBXBuildFile", fileRef=products[worker_name],
    settings={"ATTRIBUTES": ["CodeSignOnCopy"]})
objects[targets["VoiceMemoryMac"]]["buildPhases"].append(add("pdf-worker-embed", "PBXCopyFilesBuildPhase",
    buildActionMask="2147483647", dstPath="$(CONTENTS_FOLDER_PATH)/Helpers", dstSubfolderSpec="16", files=[worker_embed],
    name="Embed PDF text worker", runOnlyForDeploymentPostprocessing="0"))
proxy = add("watchproxy", "PBXContainerItemProxy", containerPortal=project_id, proxyType="1",
            remoteGlobalIDString=targets["VoiceMemoryWatch"], remoteInfo="VoiceMemoryWatch")
dependency = add("watchdependency", "PBXTargetDependency", target=targets["VoiceMemoryWatch"], targetProxy=proxy)
embed = add("watchembedfile", "PBXBuildFile", fileRef=products["VoiceMemoryWatch"],
            settings={"ATTRIBUTES": ["RemoveHeadersOnCopy"]})
copy = add("watchembed", "PBXCopyFilesBuildPhase", buildActionMask="2147483647",
           dstPath="$(CONTENTS_FOLDER_PATH)/Watch", dstSubfolderSpec="16", files=[embed],
           name="Embed Watch Content", runOnlyForDeploymentPostprocessing="0")
objects[targets["VoiceMemory"]]["dependencies"] = [dependency]
objects[targets["VoiceMemory"]]["buildPhases"].append(copy)
product_group = add("products", "PBXGroup", children=list(products.values()), name="Products", sourceTree="<group>")
group = add("root", "PBXGroup", children=[config, ui_resource, info_resource, shortcut_resource, watch_shortcut_resource, brand_resource] + list(icon_resources.values()) + sorted(refs.values()) + node_resources + [product_group], sourceTree="<group>")
project_configs = [
    add("project" + mode, "XCBuildConfiguration", buildSettings={}, name=mode)
    for mode in ["Debug", "Release"]
]
configuration = add("projectconfigs", "XCConfigurationList", buildConfigurations=project_configs,
                    defaultConfigurationIsVisible="0", defaultConfigurationName="Release")
add("project", "PBXProject", attributes={"BuildIndependentTargetsInParallel": "YES", "LastUpgradeCheck": "2660"},
    buildConfigurationList=configuration, compatibilityVersion="Xcode 14.0",
    developmentRegion="zh-Hans", hasScannedForEncodings="0", knownRegions=["zh-Hans", "en", "Base"],
    mainGroup=group, productRefGroup=product_group, projectDirPath="", projectRoot="",
    targets=list(targets.values()))


def plist(value, indent=0):
    pad = "\t" * indent
    if isinstance(value, dict):
        return "{\n" + "".join(f'{pad}\t{json.dumps(k)} = {plist(v, indent + 1)};\n'
                               for k, v in value.items()) + pad + "}"
    if isinstance(value, list):
        return "(\n" + "".join(f"{pad}\t{plist(v, indent + 1)},\n" for v in value) + pad + ")"
    return json.dumps(str(value), ensure_ascii=False)


project = ROOT / "VoiceMemory.xcodeproj"
project.mkdir(exist_ok=True)
(project / "project.pbxproj").write_text("// !$*UTF8*$!\n" + plist({
    "archiveVersion": "1", "classes": {}, "objectVersion": "56",
    "objects": objects, "rootObject": project_id
}) + "\n")
schemes = project / "xcshareddata/xcschemes"
schemes.mkdir(parents=True, exist_ok=True)
for name, target in targets.items():
    if name == worker_name:continue
    reference = f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{target}" BuildableName="{name}.app" BlueprintName="{name}" ReferencedContainer="container:VoiceMemory.xcodeproj"/>'
    (schemes / f"{name}.xcscheme").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2660" version="1.7">
  <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES">
    <BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{reference}</BuildActionEntry></BuildActionEntries>
  </BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"/>
  <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" allowLocationSimulation="YES">
    <BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable>
  </LaunchAction>
  <ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES"><BuildableProductRunnable runnableDebuggingMode="0">{reference}</BuildableProductRunnable></ProfileAction>
  <AnalyzeAction buildConfiguration="Debug"/>
  <ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
""")
print(project)
