import XCTest
@testable import VoiceMemoryCore

final class ModelConfigurationTests: XCTestCase {
    func testAPIConfigurationNormalizesAndRoundTripsWithoutCredentialFields() throws {
        var profile = ModelConfiguration()
        profile.endpoint = " https://models.example.invalid/v1/ "
        profile.model = " example-model "
        let normalized = try profile.validated()
        XCTAssertEqual(normalized.endpoint, "https://models.example.invalid/v1")
        XCTAssertEqual(normalized.model, "example-model")
        let data = try JSONEncoder().encode(normalized)
        XCTAssertEqual(try JSONDecoder().decode(ModelConfiguration.self, from: data), normalized)
        let fields = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(Set(fields.keys), ["mode", "endpoint", "model"])
    }
    func testCredentialsInURLsAndUnencryptedServicesAreRejected() {
        for endpoint in ["http://example.invalid/v1", "http://localhost:11434/v1", "https://user:password@example.invalid/v1", "https://example.invalid?key=test", "https://example.invalid/#secret", "file:///tmp/model"] {
            var profile = ModelConfiguration(); profile.endpoint = endpoint; profile.model = "example"
            XCTAssertThrowsError(try profile.validated(), endpoint)
        }
    }
    func testIncompleteAPISettingsCannotBeSavedAsValid() {
        var profile = ModelConfiguration()
        XCTAssertThrowsError(try profile.validated())
        profile.endpoint = "https://example.invalid/v1"
        XCTAssertThrowsError(try profile.validated())
    }
    func testOfficialAndDeferredModesDoNotRequireInventedConnectionDetails() throws {
        for mode in [ModelConfiguration.Mode.managed, .unconfigured] {
            var profile = ModelConfiguration(); profile.mode = mode
            profile.endpoint = "https://example.invalid/?key=not-a-real-key"
            XCTAssertNoThrow(try profile.validated())
            XCTAssertEqual(try profile.validated().endpoint, "")
        }
    }
}
