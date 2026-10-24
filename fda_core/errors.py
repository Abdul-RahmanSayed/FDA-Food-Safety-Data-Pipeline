"""Errors preserved for compatibility with the original analyzer."""

class Part2Error(Exception):
  pass


class UsageError(Part2Error):
  pass


class DataError(Part2Error):
  pass
