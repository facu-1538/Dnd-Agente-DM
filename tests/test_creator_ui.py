import unittest
from streamlit.testing.v1 import AppTest

HARNESS = '''
import streamlit as st
from ui.character_creator import render_creator
if result := render_creator("test"):
    st.session_state["confirmed"] = result
'''


class CreatorUITests(unittest.TestCase):
    def test_four_classes_can_be_selected_reviewed_and_confirmed_without_api(self):
        for class_id in ("fighter", "wizard", "rogue", "cleric"):
            with self.subTest(class_id=class_id):
                app = AppTest.from_string(HARNESS, default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertTrue(app.button(key="create_test_next").disabled)
                app.button(key="create_test_choose_" + class_id).click().run()
                app.button(key="create_test_next").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.button(key="create_test_review").disabled)
                app.text_input(key=f"create_test_{class_id}_name").input("Arin").run()
                app.button(key="create_test_review").click().run()
                self.assertFalse(app.exception)
                app.button(key="create_test_confirm").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["confirmed"]["class_id"], class_id)

    def test_changing_equipment_restricts_fighting_style(self):
        app = AppTest.from_string(HARNESS, default_timeout=30).run()
        app.button(key="create_test_choose_fighter").click().run()
        app.button(key="create_test_next").click().run()
        app.radio(key="create_test_fighter_style").set_value("Duelo").run()
        app.radio(key="create_test_fighter_gear").set_value("duelist").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.radio(key="create_test_fighter_style").value, "Defensa")

    def test_returning_to_edit_preserves_magic_choices(self):
        app = AppTest.from_string(HARNESS, default_timeout=30).run()
        app.button(key="create_test_choose_wizard").click().run()
        app.button(key="create_test_next").click().run()
        app.text_input(key="create_test_wizard_name").input("Arin").run()
        app.button(key="create_test_review").click().run()
        app.button(key="create_test_back2").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.multiselect(key="create_test_wizard_book").value), 6)
        self.assertEqual(len(app.multiselect(key="create_test_wizard_prepared").value), 3)
        self.assertFalse(app.button(key="create_test_review").disabled)
