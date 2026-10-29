"""安装后格式关联与默认应用的一次性引导。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from zipmerge.ui import integration_onboarding as onboarding  # noqa: E402
from zipmerge.util.settings import (  # noqa: E402
    KEY_POST_INSTALL_PROMPT_PENDING,
    Settings,
)

_app: QApplication | None = None


def setUpModule() -> None:  # noqa: N802
    global _app
    _app = QApplication.instance() or QApplication([])


class _FakeIntegration:
    def __init__(self) -> None:
        self.applied = []

    def candidate_extensions(self):
        return (".7z", ".rar", ".zip")

    def apply(self, extensions, **menus):
        self.applied.append((tuple(extensions), menus))


class IntegrationOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings("ZipMergeOnboardingTest", "ZipMergeOnboardingTest")
        self.settings.clear()
        self.addCleanup(self.settings.clear)
        self.integration = _FakeIntegration()

    def test_pending_prompt_associates_formats_and_opens_default_apps(self) -> None:
        self.settings.set_value(KEY_POST_INSTALL_PROMPT_PENDING, True)
        with (
            mock.patch.object(
                onboarding.QMessageBox,
                "question",
                side_effect=[
                    QMessageBox.StandardButton.Yes,
                    QMessageBox.StandardButton.Yes,
                ],
            ) as question,
            mock.patch.object(onboarding.QDesktopServices, "openUrl", return_value=True) as open_url,
        ):
            shown = onboarding.run_post_install_integration_prompt(
                None, self.settings, self.integration
            )

        self.assertTrue(shown)
        self.assertEqual(question.call_count, 2)
        self.assertEqual(self.integration.applied[0][0], (".7z", ".rar", ".zip"))
        self.assertTrue(all(self.integration.applied[0][1].values()))
        self.assertEqual(open_url.call_count, 1)
        self.assertEqual(open_url.call_args.args[0].toString(), "ms-settings:defaultapps")
        self.assertFalse(
            self.settings.value(KEY_POST_INSTALL_PROMPT_PENDING, True, bool)
        )

    def test_declining_association_keeps_menus_but_skips_default_apps(self) -> None:
        self.settings.set_value(KEY_POST_INSTALL_PROMPT_PENDING, True)
        with (
            mock.patch.object(
                onboarding.QMessageBox,
                "question",
                side_effect=[
                    QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ],
            ),
            mock.patch.object(onboarding.QDesktopServices, "openUrl") as open_url,
        ):
            onboarding.run_post_install_integration_prompt(
                None, self.settings, self.integration
            )

        self.assertEqual(self.integration.applied[0][0], ())
        self.assertTrue(all(self.integration.applied[0][1].values()))
        open_url.assert_not_called()

    def test_default_app_choice_registers_candidates_when_association_was_declined(self) -> None:
        self.settings.set_value(KEY_POST_INSTALL_PROMPT_PENDING, True)
        with (
            mock.patch.object(
                onboarding.QMessageBox,
                "question",
                side_effect=[
                    QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                ],
            ),
            mock.patch.object(onboarding.QDesktopServices, "openUrl", return_value=True),
        ):
            onboarding.run_post_install_integration_prompt(
                None, self.settings, self.integration
            )

        self.assertEqual(self.integration.applied[0][0], ())
        self.assertEqual(self.integration.applied[1][0], (".7z", ".rar", ".zip"))

    def test_normal_launch_without_pending_marker_is_silent(self) -> None:
        with mock.patch.object(onboarding.QMessageBox, "question") as question:
            shown = onboarding.run_post_install_integration_prompt(
                None, self.settings, self.integration
            )

        self.assertFalse(shown)
        question.assert_not_called()
        self.assertEqual(self.integration.applied, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
