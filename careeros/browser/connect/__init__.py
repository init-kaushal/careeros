"""Page-driving writers that send connection requests.

Sibling of `careeros/browser/fillers/`: both take a `Page` and act on it,
but a connector's signature differs enough from `Filler.fill`'s that
reusing that Protocol would distort both, so these stand alone.
"""

from careeros.browser.connect.linkedin import LinkedInConnector

__all__ = ["LinkedInConnector"]
