"""P482 owner phone/reply public-chain text, with credential positive controls."""
import _hermetic  # noqa: F401
import unittest
from agentj import privacy

ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
def b58(n):
    return (ALPHABET * 2)[:n]

class PublicChainTests(unittest.TestCase):
    def test_seal911_report_and_credentials_projection(self):
        values = [b58(n) for n in (*range(32, 45), 87, 88)] + ['0x' + 'aB19'*10, '0x' + 'aB19'*16]
        report = 'SEAL 911 报告\n' + '\n'.join('地址/交易: `' + v + '`' for v in values)
        self.assertEqual(privacy.owner_text(report), report)
        self.assertEqual(privacy.credentials(report), [])
        self.assertEqual(privacy.owner_text(privacy.owner_text(report)), report)

    def test_explicit_secrets_stay_hidden_even_with_public_shape(self):
        for value in (b58(40), b58(44), b58(88), '0x'+'aB19'*16):
            for text in ('PRIVATE_KEY='+value, 'Authorization: Bearer '+value, 'password="'+value+'"', 'X-Api: '+value, 'X-Api-Key: '+value, 'private key: '+value, '私钥：'+value):
                with self.subTest(text=text[:18]):
                    self.assertNotIn(value, privacy.owner_text(text))
                    self.assertTrue(privacy.credentials(text))
        for text in ('sk-proj-' + b58(44), 'seed phrase: ' + 'abandon '*12,
                     '-----BEGIN PRIVATE KEY-----\n'+b58(88)+'\n-----END PRIVATE KEY-----'):
            self.assertIn(privacy.R, privacy.owner_text(text))

    def test_quoted_and_spaced_secret_labels_precede_chain_exception(self):
        labels = ('私钥', '密钥', 'private key', 'PRIVATE_KEY', 'secret',
                  'secret key', 'token', 'API key', 'password', '密码',
                  '助记词', 'mnemonic', 'seed phrase', 'recovery phrase', '秘密', '令牌', '访问令牌')
        for value in (b58(44), '0x' + 'aB19' * 16):
            for label in labels:
                for quote in ('', "'", '"'):
                    for separator in (':', '：', '='):
                        text = f'{quote}{label}{quote}{separator} {quote}{value}{quote}'
                        with self.subTest(label=label, quote=quote, separator=separator):
                            self.assertNotIn(value, privacy.owner_text(text))
                            found = privacy.credentials(text)
                            self.assertTrue(found)
                            self.assertEqual(found[0][1], value)
                            self.assertEqual(privacy.owner_text(privacy.owner_text(text)), privacy.owner_text(text))

        phrase = 'abandon ' * 11 + 'about'
        for label in ('助记词', 'mnemonic', 'seed phrase'):
            for quote in ('', "'", '"'):
                text = f'{quote}{label}{quote}: {quote}{phrase}{quote}'
                self.assertNotIn(phrase, privacy.owner_text(text))
                self.assertNotIn('about', privacy.owner_text(text))
                self.assertTrue(privacy.credentials(text))

    def test_public_labels_and_unlabelled_values_remain_copyable(self):
        for value in (b58(44), '0x' + 'aB19' * 16):
            for label in ('', '地址', 'address', 'tx', '签名', 'hash'):
                text = f'"{label}": "{value}"' if label else value
                self.assertEqual(privacy.owner_text(text), text)
                self.assertEqual(privacy.credentials(text), [])

    def test_other_random_lengths_and_strict_channels_remain_masked(self):
        for n in (45, 64, 86, 89):
            self.assertIn(privacy.R, privacy.owner_text(b58(n)))
        self.assertIn(privacy.R, privacy.redact(b58(44)))
        self.assertIn(privacy.R, privacy.redact(b58(88), paths=True))

if __name__ == '__main__':
    unittest.main()
