import QtQuick
import QtTest
import "../../PanelScroll.js" as PanelScroll

TestCase {
  name: "PanelSectionScroll"

  function test_short_section_reveals_header_and_complete_body() {
    const target = PanelScroll.sectionTargetY(240, 100, 600, 360, 430, 8)

    compare(target, 352)
    verify(360 >= target)
    verify(430 <= target + 100)
  }

  function test_inputs_section_reveals_panel_header_and_close_button() {
    const target = PanelScroll.sectionTargetY(240, 100, 600, 360, 430, 8, true)

    compare(target, 0)
  }

  function test_tall_section_prioritizes_header_and_maximum_body() {
    const target = PanelScroll.sectionTargetY(100, 180, 900, 520, 820, 8)

    compare(target, 512)
    verify(520 >= target)
    verify(target + 180 > 520)
  }

  function test_section_target_is_clamped_at_content_edges() {
    compare(PanelScroll.sectionTargetY(0, 200, 500, 4, 80, 8), 0)
    compare(PanelScroll.sectionTargetY(250, 200, 500, 480, 520, 8), 300)
  }
}
