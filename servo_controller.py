import time
import logging
from DRV8825 import DRV8825, SIMULATION_MODE

# Shared app logger by name — importing it from feeder_core would be circular
log = logging.getLogger('petfeedr')

# Base unit calibration (2026-01-11)
# One dispense cycle = 100 steps ≈ 1/4 cup
BASE_STEPS = 100
AGITATION_STEPS = 40

# Portion sizes defined as number of dispense cycles
# Each cycle dispenses ~1/4 cup for consistency
PORTION_SIZES = {
    'small':  (1, '~1/4 cup'),   # 1 cycle
    'medium': (2, '~1/2 cup'),   # 2 cycles  
    'large':  (3, '~3/4 cup'),   # 3 cycles
}

# Default portion if none specified
DEFAULT_PORTION = 'small'

# Graze: the same portion as small pulses spread over GRAZE_SPAN_S.
# Bench test 2026-11-14 (kitchen scale, ½-cup portions): 4 × 50-step pulses
# with a jiggle every 2nd weighed 51.8 g against 51.7–56.8 g for a regular
# feed; with no jiggle, 40.3 g. The backward jiggle moves food, not just
# jams, so a graze keeps the regular ratio of one jiggle per BASE_STEPS.
GRAZE_PULSE_STEPS = 50
GRAZE_JIGGLE_EVERY = BASE_STEPS // GRAZE_PULSE_STEPS
GRAZE_SPAN_S = 1200


def get_portion_cycles(portion_name):
    """Get the number of dispense cycles for a given portion size."""
    if portion_name in PORTION_SIZES:
        return PORTION_SIZES[portion_name][0]
    return PORTION_SIZES[DEFAULT_PORTION][0]


def get_portion_description(portion_name):
    """Get the human-readable description for a portion size."""
    if portion_name in PORTION_SIZES:
        return PORTION_SIZES[portion_name][1]
    return PORTION_SIZES[DEFAULT_PORTION][1]


def graze_pulse_count(portion_name):
    """Pulses that add up to the portion's regular net steps (small 2, medium 4, large 6)."""
    return get_portion_cycles(portion_name) * BASE_STEPS // GRAZE_PULSE_STEPS


def _motor():
    motor = DRV8825(dir_pin=13, step_pin=19, enable_pin=12, mode_pins=(16, 17, 20))
    # Use 1/16 microstepping for quieter operation and better torque management
    motor.SetMicroStep('softward', '1/16step')
    return motor


def log_completed(elapsed, portion, source):
    """The "Feeding completed" line — the single dispense record feeding_stats parses."""
    prefix = "[SIM] ✅ " if SIMULATION_MODE else ""
    log.info(f"{prefix}Feeding completed in {elapsed:.2f}s ({portion} portion, {source})")


def trigger_pulse(jiggle):
    """One graze pulse: GRAZE_PULSE_STEPS net forward, optionally after the
    backward jiggle. The motor is de-energized afterwards — a graze waits
    minutes between pulses, and a held coil hums and runs warm for nothing.
    Returns the elapsed seconds."""
    start_time = time.time()
    Motor1 = None
    try:
        Motor1 = _motor()
        extra = AGITATION_STEPS if jiggle else 0
        if jiggle:
            Motor1.TurnStep(Dir='backward', steps=AGITATION_STEPS, stepdelay=0.0005)
            time.sleep(0.1)
        Motor1.TurnStep(Dir='forward', steps=GRAZE_PULSE_STEPS + extra, stepdelay=0.0005)
        return time.time() - start_time
    finally:
        if Motor1:
            Motor1.Stop()


def trigger_servo(portion='small', source='scheduled'):
    """Trigger the motor to dispense food.

    Args:
        portion: Size of portion to dispense ('small', 'medium', 'large')
        source: 'scheduled' or 'manual' — recorded in the completed log
            line, which is the single source of truth for feeding stats

    Larger portions are dispensed as multiple cycles of the base unit
    for more consistent results. Returns the elapsed seconds.
    """
    start_time = time.time()
    Motor1 = None
    
    # Get number of cycles for the requested portion
    num_cycles = get_portion_cycles(portion)
    portion_desc = get_portion_description(portion)

    try:
        Motor1 = _motor()

        log.info(f"Dispensing {portion} portion ({portion_desc}) - {num_cycles} cycle(s)")

        for cycle in range(num_cycles):
            if num_cycles > 1:
                log.info(f"  Cycle {cycle + 1}/{num_cycles}")
            
            # 1. Agitation: Rotate backward slightly to dislodge any jams
            Motor1.TurnStep(Dir='backward', steps=AGITATION_STEPS, stepdelay=0.0005)
            time.sleep(0.1)

            # 2. Dispense: Rotate forward (base steps + recover agitation)
            total_steps = BASE_STEPS + AGITATION_STEPS
            Motor1.TurnStep(Dir='forward', steps=total_steps, stepdelay=0.0005)
            
            # Brief pause between cycles
            if cycle < num_cycles - 1:
                time.sleep(0.3)

        elapsed = time.time() - start_time
        log_completed(elapsed, portion, source)
        return elapsed

    finally:
        # Errors propagate to feed_pet (which logs and alerts) — but the
        # motor is always de-energized, exception or not
        if Motor1:
            Motor1.Stop()
            log.debug("Motor stopped and cleaned up")
