"""The automated test channel: scenarios written as data, run against the
real handlers.

This package exists so a person — or another AI — who does not know this
codebase can still test it. The rule it is built around: a test case must
be expressible as a file of facts (say this, expect that), never as Python
that has to import the right module and build the right fake.

Nothing here is shipped to any runtime. The Application and Data tiers are
imported in-process; no port is opened, no credential is read, no network
call is made. See README.md for the contract.
"""
