import re
from enum import Enum

STATUS_TO_PRIORITY = {
    'success': 0,
    'pending': 1,
    'approved': 2,
    '': 3,
    'error': 4,
    'failure': 5,
}

INTERRUPTED_BY_HOMU_FMT = 'Interrupted by Homu ({})'
INTERRUPTED_BY_HOMU_RE = re.compile(r'Interrupted by Homu \((.+?)\)')
DEFAULT_TEST_TIMEOUT = 3600 * 10

WORDS_TO_ROLLUP = {
    'rollup-': 0,
    'rollup': 1,
    'rollup=maybe': 0,
    'rollup=never': -1,
    'rollup=always': 1,
}


class LabelEvent(Enum):
    APPROVED = 'approved'
    REJECTED = 'rejected'
    CONFLICT = 'conflict'
    SUCCEED = 'succeed'
    FAILED = 'failed'
    TRY = 'try'
    TRY_SUCCEED = 'try_succeed'
    TRY_FAILED = 'try_failed'
    EXEMPTED = 'exempted'
    TIMED_OUT = 'timed_out'
    INTERRUPTED = 'interrupted'
    PUSHED = 'pushed'
