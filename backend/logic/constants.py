"""
═══════════════════════════════════════════════════════════════════════════════
CAMEO CHEMICALS: CONSTANTS AND CONFIGURATION
Safety-Critical Module - Based on CAMEO_MASTER_LOGIC_SPEC_V5
═══════════════════════════════════════════════════════════════════════════════
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict


class Compatibility(Enum):
    """CAMEO Standard Compatibility Codes"""
    COMPATIBLE = 'C'
    CAUTION = 'I-C'
    INCOMPATIBLE = 'I'
    NO_DATA = 'N'


class HazardCode(Enum):
    """Hazard type codes"""
    HEAT = 'HEAT'
    FIRE = 'FIRE'
    EXPLOSION = 'EXPLOSION'
    INERT_GAS = 'INERT_GAS'
    FLAMMABLE_GAS = 'FLAMMABLE_GAS'
    TOXIC_GAS = 'TOXIC_GAS'
    CORROSIVE_GAS = 'CORROSIVE_GAS'
    TOXIC_SOLUTION = 'TOXIC_SOLUTION'
    POLYMERIZATION = 'POLYMERIZATION'
    VIOLENT_REACTION = 'VIOLENT_REACTION'
    SPONTANEOUS_IGNITION = 'SPONTANEOUS_IGNITION'


@dataclass
class CompatibilityInfo:
    priority: int
    color_hex: str
    color_name: str
    label_fa: str
    label_en: str
    action_required: str


COMPATIBILITY_MAP: Dict[Compatibility, CompatibilityInfo] = {
    Compatibility.COMPATIBLE: CompatibilityInfo(
        priority=1,
        color_hex='#22C55E',
        color_name='green',
        label_fa='سازگار',
        label_en='Compatible',
        action_required='Storage permitted'
    ),
    Compatibility.CAUTION: CompatibilityInfo(
        priority=2,
        color_hex='#EAB308',
        color_name='yellow',
        label_fa='احتیاط',
        label_en='Caution',
        action_required='Check SDS, separation recommended'
    ),
    Compatibility.INCOMPATIBLE: CompatibilityInfo(
        priority=3,
        color_hex='#DC2626',
        color_name='red',
        label_fa='ناسازگار',
        label_en='Incompatible',
        action_required='Must not be stored together'
    ),
    Compatibility.NO_DATA: CompatibilityInfo(
        priority=2,  # FAIL-SAFE: Treat same as CAUTION
        color_hex='#F97316',
        color_name='orange',
        label_fa='نامشخص',
        label_en='No Data',
        action_required='No data available - exercise caution'
    )
}

# Special group IDs
WATER_GROUP_ID = 100  # Water and Aqueous Solutions (verified against chemicals.db)
AIR_GROUP_ID = 101

# Map existing DB compatibility values to our enum
DB_COMPATIBILITY_MAP = {
    'Compatible': Compatibility.COMPATIBLE,
    'Caution': Compatibility.CAUTION,
    'Incompatible': Compatibility.INCOMPATIBLE,
    'C': Compatibility.COMPATIBLE,
    'I-C': Compatibility.CAUTION,
    'I': Compatibility.INCOMPATIBLE,
    'N': Compatibility.NO_DATA,
}
