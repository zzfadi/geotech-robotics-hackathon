"""Regression: a map reload must not create an editor placement transaction."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

MODULE = Path(__file__).resolve().parents[1] / 'Plugins/CoPresence/Content/Python/copresence/local_presence.py'

class PresenceTests(unittest.TestCase):
    def load(self, library, existing=()):
        unreal = types.ModuleType('unreal')
        unreal.CoPresenceFabLibrary = library
        unreal.GameplayStatics = types.SimpleNamespace(get_all_actors_of_class=lambda *_: existing)
        # Any use of the editor factory is the original regression, even for transient=True.
        unreal.get_editor_subsystem = Mock(side_effect=AssertionError('Editor placement must not be used'))
        unreal.Name = str
        unreal.TextRenderComponent = object
        package = types.ModuleType('copresence')
        package.agent_editor = types.SimpleNamespace(is_agent_editor=lambda: True)
        package.tracking = types.SimpleNamespace(LOCAL_ONLY_TAG='LocalOnly', agent_name=lambda:'Test')
        spec = importlib.util.spec_from_file_location('presence_test_subject', MODULE)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'unreal':unreal,'copresence':package}):
            spec.loader.exec_module(module)
        return module

    def test_old_native_build_does_not_fall_back_to_editor_factory(self):
        module = self.load(types.SimpleNamespace())
        module.ensure(object(), object())
        self.assertIsNone(module.actor)

    def test_busy_native_spawn_can_retry_without_partial_marker(self):
        spawn = Mock(return_value=None)
        module = self.load(types.SimpleNamespace(spawn_local_presence=spawn))
        module.ensure(object(), object())
        self.assertIsNone(module.actor)
        spawn.assert_called_once()

    def test_new_marker_uses_native_spawn_and_non_dirty_label(self):
        marker=Mock()
        marker.get_name.return_value='CoPresenceSelf_test'
        marker.get_components_by_class.return_value=[]
        spawn=Mock(return_value=marker)
        module=self.load(types.SimpleNamespace(spawn_local_presence=spawn))
        world=Mock(); ghost_class=object()
        module.ensure(world,ghost_class)
        spawn.assert_called_once_with(ghost_class)
        marker.set_actor_label.assert_called_once_with('CoPresence_LocalAgentEyes',mark_dirty=False)
        marker.rename.assert_not_called()
        self.assertIn('CoPresenceSelf',marker.tags)

if __name__ == '__main__': unittest.main()
