"""Unit tests for stixmd.fetch (no network)."""
import unittest

from stixmd.config import EXIT_FETCH, SOURCE_URL, StixMdError
from stixmd.fetch import NoRedirect, validate_url


class ValidateUrlTests(unittest.TestCase):
    def test_accepts_source_url(self) -> None:
        validate_url(SOURCE_URL)
        validate_url("https://DOCS.oasis-open.org:443/cti/stix/v2.1/os/stix-v2.1-os.html")

    def test_rejects(self) -> None:
        bad = ["http://docs.oasis-open.org/x.html", "https://docs.oasis-open.org.evil.com/x.html",
               "https://user@docs.oasis-open.org/x.html", "https://docs.oasis-open.org:8443/x.html",
               "https://docs.oasis-open.org/x.pdf", "https://docs.oasis-open.org/x.html?a=1",
               "file:///C:/x.html", "https://docs.oasis-open.org/x.html#frag",
               "https://docs.oasis-open.org" + chr(9) + "/x.html", "https://docs.oasis-open.org:/x.html",
               " https://docs.oasis-open.org/x.html"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(StixMdError) as ctx:
                validate_url(url)
            self.assertEqual(ctx.exception.exit_code, EXIT_FETCH)

    def test_redirects_refused(self) -> None:
        with self.assertRaises(StixMdError) as ctx:
            NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/x.html")
        self.assertEqual(ctx.exception.exit_code, EXIT_FETCH)


if __name__ == "__main__":
    unittest.main()
