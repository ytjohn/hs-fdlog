# timesync.py
#
# set and track computer clock to GPS

# Installation and Use
#
# 0) Install Python
# 1) Install PyWin32
# 2) Install PySerial
#
# 3) Set GPS to send NMEA0183 Sentences at 4800 baud
# 4) Connect GPS to serial port
# 5) Set clock (approx) and timezone
# 6) Run 'timesync.py'

# This code is based on code originally written by Eric Williams WD6CMU
# Eric's original code in CVS
# Rewritten by Alan Biocca WB6ZQZ  June 2006

# Thoughts on GPS time readouts by akb
#
# Navigation GPS' don't put out precise time info. They typically
# send the time out about once per second, but the time reported
# is likely a one second resolution sample from the recent past.
# Two important facts emerge from this - one is that the time is
# delayed by some amount (at least the serialization time of the
# baud rate, plus some more). The other is the resolution is one
# second. Thus the time is 'correct' when within this range, and
# should be corrected when out of this range.
#
# Another concern is the time correction method. It is undesirable
# for the system clock to ever go 'backwards', at least down to the
# 'seconds' resolution readout.
#
# Drifting the time
# only fiddle with small chunk of time (Eric did 100ms bumps)
# and selected to not cross second boundary to avoid ripple
# even better to adjust clock rate, someday look into that

# Principles of Operation
#
# Read GPS NMEA sentence via serial port
#   GPS time is sampled to 1s, then nav calcs done, then serialized
#   This makes considerable delay and ambiguity in the time
# Correct GPS time by adding GPS_RECEIVER_DELAY to GPS time
#   This corresponds to the time added by the GPS receiver
#   Due to the 1s sampling and delays the GPS time can come no earlier
#   than this but it can be later. Adjust this empirically to get phase
#   of second rollover right. (Per GPS).
# Clamp error due to 1s sampling resolution
#   make a deadband window, don't correct when inside this window
# Added a creep mode that uses the IIR filtered err to guide small changes
#   within the deadband. The filter is adjusted for the change.

# Testing Notes
#
# Tested with Garmin Etrex Vista. Has annoying habit of waiting for keypress
# to ack poor satellite reception.
# Tested with Garmin 12xl. It beeps and requires button push when signals
# get weak. Note - the 12xl clears the beeps when the satellites are
# re-acquired automatically. Noisy, but nice. Beeper can be disabled.
#
# Testing with load on the computer - this reduces the stability of
# the tracking. The cpu gets tied up and some GPS readings get delayed.
# Thinking about ways to detect this - one way is to observe the cadence
# of the readouts - and discard those that seem 'late'. Two readings of
# error are taken and the time between checked for reasonable cadence.
# if not, a retry ensues. A threaded version was tried and worked, but
# this is simpler.

print "TimeSync  ECWilliams / AKBiocca 6/2006"
print "$Revision: 1.8 $ $Date: 2006/06/13 02:38:00 $"
print

import time, calendar, sys
try:
    import win32api                 # from package pyWin32
    import serial                   # from package pySerial
except:
    print "fatal error - required module not available during import"
    raise

def find_gps():
    'Find a GPS on a serial port'

    print "scanning COM ports looking for GPS"
    for tport in range(10):
        try:
            serdev = None
            #print "trying COM%d" % (tport+1)
            serdev = serial.Serial(port=tport,baudrate=4800,timeout=2)
            print "found serial port COM%d"%(tport+1)
            for n in range(3) :
                line = serdev.readline()
                print "  serial:",line,
                head = line[0:3]
                if head == '$GP' or head == '$PG':
                    print "found GPS receiver on COM%d" % (tport+1)
                    for o in range(15):                 # show some GPS data
                        print "  gps:",serdev.readline(),
                    return serdev
            serdev.close()
        except:
##            print "  error on COM%d, skipping" % (tport+1)
            if serdev: serdev.close()

    print "no GPS receiver found on serial ports, exiting"
    sys.exit(1)

GPS_RECEIVER_DELAY = 0.9        # delay in S fm sample to end of sentence (empirical)
number_satellites = -1          # number of satellites in view

def read_gps_time():
    'read date/time from GPS NMEA0183 string'
    global number_satellites,gpsdev
    n = 0
    while True:
        gpsdev.flushInput()                                 # clear out old input
        for i in range(50):
            line = gpsdev.readline()
            #print '  gps',line,
            parts = line.split(',')
            if parts[0] == '$GPRMC' and parts[2] == 'A' :   # Valid position/time sentence
##                print "time",line,
                gpstime = parts[1][0:6]                     # hhmmss
                gdate = parts[9]                            # ddmmyy
                gpsdate = gdate[4:6]+gdate[2:4]+gdate[0:2]  # yymmdd
##                print "%s %s GMT"%(gpsdate,gpstime)
                return "%s %s GMT"%(gpsdate,gpstime)
            if parts[0] == '$GPGGA' :
                nsat = int(parts[7])                        # sats in view
                if nsat != number_satellites:
                    print "satellites in view now",nsat
                    number_satellites = nsat
        print "waiting for valid GPS time",n
        n += 1

def find_time_error():
    'find system clock error, + means it is reading ahead or fast'
    gps_time = read_gps_time()
    sysclock = time.time()
    gpsclock = calendar.timegm(time.strptime(gps_time,"%y%m%d %H%M%S %Z"))
    gpsclock += GPS_RECEIVER_DELAY
    return sysclock - gpsclock

# Cadence Detection

standard = 2.0                      # standard cadence, initial value
max_variation = 0.2                 # maximum allowed variation

def get_time_error():
    'get error, checking cadence of two reads'
    global standard
    e1 = find_time_error()
    t1 = time.time()
    while True:
        e2 = find_time_error()
        t2 = time.time()
        cadence = t2 - t1
        if cadence < 2.5:
            standard = standard*0.9 + cadence*0.1       # track cadence
        #print e1,e2,cadence,standard
        if abs(cadence - standard) <= max_variation:
            e = (e1 + e2) * 0.5                         # return average
            return e
        e1,t1 = e2,t2
        print "bad cadence retry %.3f %.3f"%(cadence,standard)

correction_max = 500                # max correction ms per bump
plus_bumps = 0
minus_bumps = 0

# bump avoids changes to the clock close to the changing of seconds (ripple)
def bump_system_clock(t):
    ''' bump system clock second-synchronously by up to t S (max 500 ms)
        positive sets clock ahead. '''
    global plus_bumps,minus_bumps
    t = int(t * 1000)           # convert S to ms
    if   t > correction_max:  t = correction_max
    elif t < -correction_max: t = -correction_max
    elif t == 0: return
    if t > 0: plus_bumps += t/1000.
    if t < 0: minus_bumps += t/1000.
    while True:
        yr, mon, wd, md, hr, mn, sec, ms = win32api.GetSystemTime()
        if (t < 0 and ms > 600 and ms < 900) or (t >= 0 and ms > 100 and ms < 400):
            ms += t
            win32api.SetSystemTime(yr, mon, wd, md, hr, mn, sec, ms)
            print "bumped system clock",t,"ms",plus_bumps,minus_bumps
            return
        time.sleep(0.05)

excessive_error = 300           # quit on excessive time error
warp = 0.5                      # large clock bumps at startup
impulse = 0.1                   # fast tracking adjustment
thruster = 0.01                 # creep on thrusters
fc = 0.01                       # filter coeff

def correct_system_time():
    'read and evaluate time error, leave deadband'

    sat = -1
    fe = 0                                      # filtered error
    print "assess initial clock error"
    for n in range(10):
        e = get_time_error()
        print "err %6.3f" % (e)
        fe += e*0.1
    print "initial average err %.3f" % fe

    if abs(fe) > excessive_error:
        print "exiting due to excessive clock error"
        print "reset clock and check timezone"
        return

    while abs(fe) > 2*warp:                     # warp to GPS clock settings
        if fe > 0: cor = -warp
        if fe < 0: cor = warp
        bump_system_clock(cor)
        fe += cor

    while True:
        e = get_time_error()
        fe = e*fc + (1-fc)*fe                   # filter error
        print "err %6.3f filtered %6.3f" % (e,fe)

        if abs(fe) > 2*impulse:                 # fast tracking
            if fe > 0: cor = -impulse
            else: cor = impulse
            bump_system_clock(cor)
            fe += cor

        elif abs(fe) > 2*thruster:              # creep
            if fe > 0: cor = -thruster
            else: cor = thruster
            bump_system_clock(cor)
            fe += cor

gpsdev = find_gps()

#bump_system_clock(0.5)                          # force off sync for testing
#bump_system_clock(0.5)

correct_system_time()

# eof
