import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from game_engine import state_manager
from game_engine.character_creation import default_choices


class CharacterStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dm-character-test-")
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.paths = patch.object(state_manager, "PARTIDAS_DIR", self.root / "partidas")
        self.singleton = patch.object(state_manager.StateManager, "_instance", None)
        self.paths.start()
        self.singleton.start()
        self.sm = state_manager.get_state_manager()

    def tearDown(self):
        self.singleton.stop()
        self.paths.stop()
        self.temp.cleanup()

    def test_confirmation_persists_complete_sheet_and_survives_damage(self):
        hero = self.sm.confirm_character(dict(default_choices("wizard"), name="Arin"))
        saved = json.loads(self.sm.ruta_estado.read_text(encoding="utf-8"))["jugador"]
        self.assertEqual(saved, hero)
        self.sm.damage_player(7, "trampa")
        after = self.sm.get_player_info()
        self.assertEqual(after["hp"], 93)
        for field in ("atributos", "modificadores", "ca", "ataques", "equipo", "magia", "habilidades"):
            self.assertEqual(after[field], hero[field])

    def test_existing_character_cannot_be_overwritten(self):
        hero = self.sm.confirm_character(dict(default_choices("fighter"), name="Arin"))
        with self.assertRaises(ValueError):
            self.sm.confirm_character(dict(default_choices("wizard"), name="Otro"))
        self.assertEqual(self.sm.get_player_info(), hero)

    def test_old_saved_character_remains_playable(self):
        self.sm.set_character("Viejo", "elfo", "Mago", 2)
        self.assertEqual(self.sm.get_player_info()["raza"], "elfo")
        self.sm.heal_player(5)
        self.assertEqual(self.sm.get_player_info()["nivel"], 2)
