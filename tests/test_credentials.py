"""Mocked native credential boundaries, plus explicitly opt-in isolated smoke."""

import ctypes
import os
import unittest
from unittest.mock import Mock, patch

from saga import credentials as module
from saga.credentials import CredentialError, CredentialStore


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.CredReadW.return_value = 0
        self.api.CredWriteW.return_value = 1
        self.api.CredDeleteW.return_value = 1
        loader = patch.object(module, '_load_api', return_value=self.api)
        loader.start()
        self.addCleanup(loader.stop)
        error = patch.object(ctypes, 'get_last_error', return_value=1168, create=True)
        self.last_error = error.start()
        self.addCleanup(error.stop)
        self.store = CredentialStore()

    def provide(self, value):
        self.buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        self.native = module._CREDENTIALW()
        self.native.CredentialBlob = ctypes.cast(self.buffer, module._BYTE_POINTER)
        self.native.CredentialBlobSize = len(value)

        def read(target, kind, flags, output):
            ctypes.cast(output, ctypes.POINTER(module._PCREDENTIAL))[0] = ctypes.pointer(self.native)
            return 1

        self.api.CredReadW.side_effect = read

    def test_absence_is_normal_and_uses_fixed_target(self):
        self.assertIsNone(self.store.read())
        self.assertFalse(self.store.exists())
        self.assertEqual(self.api.CredReadW.call_args.args[:3],
                         ('Saga/OpenAI/report-drafting', 1, 0))
        self.api.CredFree.assert_not_called()

    def test_read_and_exists_clear_and_free_native_allocation(self):
        for exists in (False, True):
            self.provide(b'sk-synthetic-test')
            self.assertEqual(self.store.exists() if exists else self.store.read(),
                             True if exists else 'sk-synthetic-test')
            self.assertEqual(bytes(self.buffer), bytes(len(self.buffer)))
        self.assertEqual(self.api.CredFree.call_count, 2)

    def test_read_failure_is_not_absence(self):
        self.last_error.return_value = 5
        for operation in (self.store.read, self.store.exists):
            with self.assertRaisesRegex(CredentialError, 'could not read'):
                operation()
        self.api.CredFree.assert_not_called()

    def test_exists_never_copies_or_decodes_credential(self):
        self.provide(b'\xffsynthetic')
        with patch.object(ctypes, 'string_at', side_effect=AssertionError('must not copy')):
            self.assertTrue(self.store.exists())
        self.assertEqual(bytes(self.buffer), bytes(len(self.buffer)))
        self.api.CredFree.assert_called_once()

    def test_invalid_native_values_are_freed_and_sanitized(self):
        for value in (b'key\x00secret', b'key secret', b'\xffsecret', b''):
            self.provide(value)
            with self.assertRaises(CredentialError) as caught:
                self.store.read()
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(bytes(self.buffer), bytes(len(self.buffer)))
        self.assertEqual(self.api.CredFree.call_count, 4)

    def test_oversize_native_value_is_rejected_and_freed(self):
        self.provide(b'x' * 2561)
        with self.assertRaises(CredentialError):
            self.store.read()
        self.api.CredFree.assert_called_once()

    def test_save_and_replace_use_same_user_local_target_and_clear_buffers(self):
        observed = []

        def write(pointer, flags):
            item = ctypes.cast(pointer, module._PCREDENTIAL).contents
            observed.append((item.TargetName, item.Type, item.Persist, flags,
                             ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)))
            return 1

        self.api.CredWriteW.side_effect = write
        with patch.object(module.ctypes, 'memset', wraps=ctypes.memset) as clear:
            self.store.save('sk-first-synthetic')
            self.store.save('sk-second-synthetic')
        self.assertEqual(observed, [
            ('Saga/OpenAI/report-drafting', 1, 2, 0, b'sk-first-synthetic'),
            ('Saga/OpenAI/report-drafting', 1, 2, 0, b'sk-second-synthetic'),
        ])
        self.assertEqual(clear.call_count, 2)
        for call in clear.call_args_list:
            self.assertEqual(bytes(call.args[0]), bytes(call.args[2]))

    def test_failed_save_clears_native_buffer_and_reports_failure(self):
        self.api.CredWriteW.return_value = 0
        with (patch.object(module.ctypes, 'memset', wraps=ctypes.memset) as clear,
              self.assertRaisesRegex(CredentialError, 'could not save')):
            self.store.save('sk-synthetic')
        self.assertEqual(bytes(clear.call_args.args[0]), bytes(12))

    def test_bad_keys_never_reach_windows(self):
        for key in ('', ' ', 'key\n', 'key\x00', 'kéy', 'x' * 2561, None, 'key:bad'):
            with self.subTest(key=key), self.assertRaises(CredentialError):
                self.store.save(key)
        self.api.CredWriteW.assert_not_called()

    def test_remove_and_absent_remove(self):
        self.store.remove()
        self.api.CredDeleteW.assert_called_once_with('Saga/OpenAI/report-drafting', 1, 0)
        self.api.CredDeleteW.return_value = 0
        self.store.remove()
        self.last_error.return_value = 5
        with self.assertRaisesRegex(CredentialError, 'could not remove'):
            self.store.remove()

    def test_no_public_custom_target(self):
        with self.assertRaises(TypeError):
            CredentialStore('another-target')


class NativeApiTests(unittest.TestCase):
    def test_windows_prototypes_and_last_error(self):
        api = Mock()
        with (patch.object(module.os, 'name', 'nt'),
              patch.object(ctypes, 'WinDLL', return_value=api, create=True) as load):
            self.assertIs(module._load_api(), api)
        load.assert_called_once_with('Advapi32.dll', use_last_error=True)
        self.assertEqual(api.CredReadW.argtypes[-1], ctypes.POINTER(module._PCREDENTIAL))
        self.assertEqual(api.CredWriteW.argtypes, [module._PCREDENTIAL, module._DWORD])
        self.assertEqual(api.CredDeleteW.argtypes, [ctypes.c_wchar_p, module._DWORD, module._DWORD])
        self.assertEqual(api.CredFree.argtypes, [ctypes.c_void_p])
        self.assertIsNone(api.CredFree.restype)
        self.assertEqual(ctypes.sizeof(module._CREDENTIALW),
                         80 if ctypes.sizeof(ctypes.c_void_p) == 8 else 52)

    def test_unavailable_and_load_errors_are_controlled(self):
        with (patch.object(module.os, 'name', 'posix'),
              self.assertRaises(CredentialError)):
            CredentialStore().read()
        with (patch.object(module.os, 'name', 'nt'),
              patch.object(ctypes, 'WinDLL', side_effect=OSError('private detail'), create=True),
              self.assertRaises(CredentialError) as caught):
            CredentialStore().save('sk-synthetic')
        self.assertNotIn('private detail', str(caught.exception))


@unittest.skipUnless(os.name == 'nt' and os.environ.get('SAGA_TEST_CREDENTIALS') == '1',
                     'Set SAGA_TEST_CREDENTIALS=1 on Windows for isolated native smoke')
class NativeCredentialSmokeTests(unittest.TestCase):
    def test_isolated_synthetic_round_trip(self):
        store = module._isolated_test_store()
        self.assertNotEqual(store._target, module._TARGET)
        self.assertTrue(store._target.startswith('Saga/tests/OpenAI/'))
        self.addCleanup(store.remove)
        self.assertIsNone(store.read())
        store.save('sk-synthetic-never-a-real-api-key')
        self.assertEqual(store.read(), 'sk-synthetic-never-a-real-api-key')
        store.save('sk-synthetic-replacement')
        self.assertEqual(store.read(), 'sk-synthetic-replacement')
        store.remove()
        self.assertFalse(store.exists())


if __name__ == '__main__':
    unittest.main()
