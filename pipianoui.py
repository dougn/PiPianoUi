import os
import re
import time
import glob

import signal
import threading

try:
    import numpy
except ImportError:
    exit("This script requires the numpy module\nInstall with: sudo pip install numpy")

try:
    import pygame
    import pygame.locals
except ImportError:
    exit("This script requires the pygame module\nInstall with: sudo pip install pygame")

try:
    import pianohat
except ImportError:
    print("ERROR: Could not find pianohat library. Will work only in 'keyboard' mode.")
    pianohat = None

try:
    import midi
    import midi.sequencer
except ImportError:
    print("ERROR: Could not find midi library. Will not load midi sequencers mode.")
    midi = None
    
_STARTUP_DELAY = 0.05
_OCTAVE_WIDTH = 43
_OCTAVE_PADDING = 3

class FlipFlopState():
    def __init__(self, prev_state=False):
        self.prev_state = bool(prev_state)
        self.state = dict()
    def toggle(self, i):
        s = not self.state.get(i, self.prev_state)
        self.state[i] = s
        return s
    def __getitem__(self, i):
        return self.state.get(i, self.prev_state)

def startup_lights(callback=None):
    """Cycle the leds on the PianoHAT in a pretty way to show things started up.
    """
    if not pianohat:
        return
    pianohat.auto_leds(False)
    led = FlipFlopState()
    for i in xrange(16):
        if i < 13:
            pianohat.set_led(i, True)
        if i-3 >= 0:
            pianohat.set_led(i-3, False)
        j = 13+(i%3)
        pianohat.set_led(j,led.toggle(j))
        time.sleep(_STARTUP_DELAY)
        if callback and callable(callback):
            callback()
    for i in xrange(16):
        pianohat.set_led(1, False)
    pianohat.auto_leds(True)

def load_img(name):
    """Load image and return an image object"""

    fullname = name
    try:
        image = pygame.image.load(fullname)
        if image.get_alpha() is None:
            image = image.convert()
        else:
            image = image.convert_alpha()
    except pygame.error as message:
        print("Error: couldn't load image: " + fullname)
        raise SystemExit(message)
    return (image, image.get_rect())

def key_maskings(width_white_key, height):
    white_keys = [0,2,4,5,7,9,11,12]
    black_keys = [1,3,6,8,10]
    black_offset = [18, 58, 115, 151, 187]
    black_fill = (125, 0, 125)
    white_fill = (0, 255, 124)
    black_mask = (0, 0, 19, 68)
    white_mask = None
    result = []
    for i in xrange(13):
        if i in white_keys:
            offset = white_keys.index(i) * width_white_key
            nick = 0
            fill = white_fill
            mask = white_mask
            blend = pygame.BLEND_SUB
        else:
            offset = black_offset[black_keys.index(i)]
            nick = 1
            fill = black_fill
            mask = black_mask
            blend = pygame.BLEND_ADD
        result.append((fill, (offset, nick), mask, blend))
    return result

def octave_maskings(width, height, hp=2, wp=3):
    width_keys = width - _OCTAVE_WIDTH
    oct_full_height = (height/11)+1
    oct_height = oct_full_height - (hp *2)
    oct_width = _OCTAVE_WIDTH - (wp * 2)
    result = [(width_keys, 0, _OCTAVE_WIDTH, height)]
    for y in xrange(1,11):
        result.append([width_keys+wp-1,
                       (height-(oct_full_height*y))+hp,
                       oct_width, oct_height])
    ## fix for some graphic odities.
    for f in xrange(1,5):
        result[-f][1] += 1
    result[-1][1] += 1
    return pygame.Surface((oct_width, oct_height)), result
    
class PiPianoUI():
    """Graphical interface for the PianoHat and multiple instruments.
    
    The PyGame window has a virtual keyboard which responds when the PianoHAT
    is interacted with. There is a graphical representation of the current
    octave and set instrument.
    
    
    The Instrument class can be inherited from to easily implement Add multiple instruments.
    """
    def __init__(self):
        pygame.init()
        pygame.font.init()
        font = pygame.font.SysFont('monospace', 14)
        screen = pygame.display.set_mode((300, 150))
        fdir = os.path.dirname(os.path.abspath(__file__))
        (key_graphic, kgrect) = load_img(os.path.join(fdir,'hat_keys.png'))
        (width, height) = (kgrect.width, kgrect.height)
        width_white_key = (width - _OCTAVE_WIDTH) / 8
        
        screen = pygame.display.set_mode((width, height + 20))
        pygame.display.set_caption("PiPianoUi")
        
        pressed = pygame.Surface((width_white_key, height))
        pressed.fill((0, 230, 0))
        
        console = pygame.Surface((width, 20))
        console.fill((255, 255, 255))
        
        screen.blit(key_graphic, (0, 0))
        pygame.display.update()
        screen.blit(console, (0, height))
        pygame.display.update()

        self.width = width
        self.width_keys = width - _OCTAVE_WIDTH
        
        self.width_white_key = width_white_key
        self.height_keys = height
        self.height_console = 20
        self.height = height + 20
        
        self.screen = screen
        self.key_graphic = key_graphic
        self.font = font
        self.console = console
        self.pressed = pressed
        self.key_blits = key_maskings(width_white_key, height)
        self.octbar, self.oct_blits = octave_maskings(width, height)
        pygame.display.update()
        self.instruments = []
        self.instrument_index = 0
        self.instrument = None
        self.add_instrument(Instrument())
        self.set_instrument(0)
        self.register()

    def register(self):
        """Register the callback methods with pianohat
        """
        if not pianohat:
            return
        startup_lights(pygame.display.update)
        pianohat.on_note(self.handle_note)
        pianohat.on_octave_up(self.handle_octave_up)
        pianohat.on_octave_down(self.handle_octave_down)
        pianohat.on_instrument(self.handle_instrument)
        pianohat.auto_leds(True)
        
    def handle_note(self, channel, pressed):
        """pianohat.on_note callback
        """
        if channel < 0 or channel > 12:
            return
        fill, rect, mask, blend = self.key_blits[channel]
        if pressed:
            self.pressed.fill(fill)
            self.screen.blit(self.pressed, rect, mask, blend)
            pygame.display.update()
            msg = self.instrument.note_on(channel, self.octave)
            self.message(msg)
        else:
            msg = self.instrument.note_off(channel, self.octave)
            self.message(msg)
            if not mask:
                mask = (0, 0, self.width_white_key, self.height_keys)
            full_mask = (rect[0], rect[1], mask[2], mask[3])
            self.screen.blit(self.key_graphic, rect, full_mask)
            pygame.display.update()

    def draw_octaves(self):
        """re-draw the octave meter with the current max octave and octave values
        """
        octmask = self.oct_blits[0]
        self.screen.blit(self.key_graphic, (octmask[0], octmask[1]), octmask)
        for i in xrange(1, min(self.octaves+1, 11)):
            octmask = self.oct_blits[i]
            self.octbar.fill((0, 155, 124))
            if i <= self.octave:
                self.screen.blit(self.octbar, (octmask[0], octmask[1]), None, pygame.BLEND_ADD)
            else:
                self.screen.blit(self.octbar, (octmask[0], octmask[1]), None, pygame.BLEND_SUB)
        pygame.display.update()

    def handle_octave_up(self, channel, pressed):
        """pianohat.on_octave_up callback
        """
        if not pressed:
            return
        self.octave = min(self.octave+1, self.octaves)            
        msg = self.instrument.octave_up(self.octave)
        self.draw_octaves()
        if msg is None:
            msg = "octave up: " + str(self.octave)
        self.message(msg)
        
    def handle_octave_down(self, channel, pressed):
        """pianohat.on_octave_down callback
        """
        if not pressed:
            return
        self.octave = max(self.octave-1, 0)
        msg = self.instrument.octave_down(self.octave)
        self.draw_octaves()
        if msg is None:
            msg = "octave down: " + str(self.octave)
        self.message(msg)

    def handle_instrument(self, channel, pressed):
        """pianohat.on_instrument callback
        """
        if not pressed:
            return
        new_inst = (self.instrument_index+1)%len(self.instruments)
        self.set_instrument(new_inst)

    def message(self, message):
        """Update the message box with the current instrument name and supplied message
        """
        if not isinstance(message, (str, unicode)):
            return
        inst = '' if not self.instrument else self.instrument.name + ' '
        print(inst+message)
        t = self.font.render(inst+message, 2, (0, 0, 0))
        self.console.fill((255, 255, 255))
        self.console.blit(t, (0, 0))
        self.screen.blit(self.console, (5, self.height_keys+5))
        pygame.display.update()
    
    def add_instrument(self, instrument):
        """Add an instance of Instrument to set of available instruments to be cycled through
        """
        self.instruments.append(instrument)
        self.message('Added instrument: '+ instrument.name)
        return len(self.instruments)
        
    def set_instrument(self, index):
        """Set the current instrument from the list of available
        """
        if index < 0 or index > len(self.instruments):
            return
        if self.instrument:
            self.instrument.deselect()
        self.instrument = self.instruments[index]
        self.instrument_index = index
        self.octaves = self.instrument.octaves
        self.octave = self.instrument.initial_octave
        m = self.instrument.select()
        self.message(m if m else '') # change the instrument name
        self.draw_octaves()
        
    def remove_instrument(self, name_or_index):
        """Remove one of the instruments
        """
        index = None
        if len(self.instruments) <=1:
            return
        if isinstance(name_or_index, str):
            for i, inst in enumerate(self.instruments):
                if inst.name == name_or_index:
                    index = i
        elif isinstance(name_or_index, int):
            index = name_or_index
        if index is None:
            return
        if self.instrument_index == index:
            self.instruments.pop(index)
            self.set_instrument(0)

_KEYS = 'C,C#,D,D#,E,F,F#,G,G#,A,A#,B,C,octave_down,octave_up,insturment'.split(',')
def key_name(channel):
        """Helper method to get the key name"""
        return _KEYS[channel]
    
class Instrument:
    """Base class for instruments to implement a PiPianoUI player.
    
    By it's self, will just display the note pressed.
    
    All instruments need to have the following members:
    
    * name
    * octaves
    * initial_octave
    
    """
    def __init__(self, name="", octaves=10, initial_octave=5):
        self.name = name
        self.octaves = octaves
        self.initial_octave = initial_octave
    
    
    def note_on(self, channel, octave):
        """Only method you need to override to play music.
        
        Called when a key is pressed.
        
        Return string is the note pressed plus octave.
        Returned string will be displayed in the message area.
        """
        return 'Note: ' + key_name(channel) + ' Octave: ' + str(octave)
    
    def note_off(self, channel, octave):
        """The key is no longer being pressed.
        
        This can be used to implement sound fading or other effects.
        
        Returned string will be displayed in the message area.
        """
        return
    
    def octave_up(self, octave):
        """Use this to set the new octave setting if needed for the player.
        
        This can be used to re-initialize pygame audio playback settings.
        
        Returned string will be displayed in the message area.
        """
        return
    
    def octave_down(self, octave):
        """Use this to set the new octave setting if needed for the player.
        
        This can be used to re-initialize pygame audio playback settings.
        
        Returned string will be displayed in the message area.
        """
        return
    
    def select(self):
        """Called when the instrument is selected
        
        Use this to initialize the instrument information for playback including
        setting up hardware such as USB Midi controllers.
        
        Returned string will be displayed in the message area.
        """
        pass
    
    def deselect(self):
        """Called when the instrument is deselected
        
        Use this to shut down audio players or hardware connections when this
        instrument is deselected.
        """
        pass
    
def natural_sort_key(s, _nsre=re.compile('([0-9]+)')):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(_nsre, s)]

class WavPlayer(Instrument):
    """Example wav file player.
    
    This is based on code from:
    https://github.com/pimoroni/Piano-HAT/blob/master/examples/simple-piano.py
    
    When given a directory and file types, it will load those files into
    list of pygame samples to be played when the appropriate key is pressed.
    
    The files mapped in order by their filenames. This assumes a 12 notes
    per octave with the first note being 'C'. The last key (the 13th) is
    the 'C' note for the next octave.
    
    The initial_octave is the middle octave detected.
    
    This plays short wav samples. A good source of such samples can be found at:
    https://freesound.org/
    """
    def __init__(self, folder, filetypes=('*.wav', '*.ogg'), loop=0):
        self.name = 'WavPlayer:'+os.path.basename(folder)
        self.folder = folder
        self.loop = loop
        self.files = []
        for filetype in filetypes:
            self.files.extend(glob.glob(os.path.join(folder, filetype)))
        self.files.sort(key=natural_sort_key)
        self.octaves = 0
        self.initial_octave = 0
        if self.files:
            self.octaves = int(len(self.files) / 12)
            self.initial_octave = int(self.octaves / 2)
        self.samples = []
        
    def note_on(self, channel, octave):
        """Play the sample for the current key and octave.
        """
        ind = channel + (octave*12)
        if ind >= len(self.samples):
            return ''
        self.samples[ind].play(loops=self.loop)
        return os.path.basename(self.files[ind])
        
    def select(self):
        """Initialize the pygame mixer, and load the samples from files.
        """
        if pianohat:
            pianohat.auto_leds(True)
        pygame.mixer.pre_init(44100, -16, 1, 512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)
        self.samples = [pygame.mixer.Sound(sample) for sample in self.files]

    def deselect(self):
        """Stop the pygame mixer, and quit it so others can initialize it with
        different settings.
        """
        samples = self.samples
        self.samples = []
        pygame.mixer.stop()
        pygame.mixer.quit()
        del samples

class Songs(WavPlayer):
    """Example sound board style music player for playing longer mp3 tracks.
    When holding down the specific key. Multiple songs can be mixed at once.
    
    Songs are mapped the same way they are with the WavPlayer.
    
    Octaves modify the song speed from 0 to 10, with 4 being normal speed.
    Depending on the bitrate, some mp3 songs will cycle in speed across the
    octave setting range.
    """
    def __init__(self, folder, filetypes=('*.mp3',), loop=0):
        WavPlayer.__init__(self, folder, filetypes, loop)
        self.name="Songs:"+os.path.basename(folder)
        self.octaves = 10
        self.initial_octave = 4
        self.last_octave = 4
        
    def select(self):
        """Initialize the pygame mixer for mp3 playback.
        """
        if pianohat:
            pianohat.auto_leds(True)
        pygame.mixer.pre_init(4411*(self.initial_octave+1), -16, 2, 2048)
        pygame.mixer.init()
    
    def note_on(self, channel, octave):
        """Load and play the mp3 file.
        Optionally re-initialize the pygame mixer if the octave is different
        from the current initialization.
        """
        ind = channel
        if ind >= len(self.files):
            return ''
        if octave != self.last_octave:
            pygame.mixer.music.stop()
            pygame.mixer.stop()
            pygame.mixer.quit()
            pygame.mixer.pre_init(4411*(octave+1), -16, 1, 512)
            self.last_octave = octave
            pygame.mixer.init()
            pygame.mixer.set_num_channels(32)
        pygame.mixer.music.load(self.files[ind])
        x = pygame.mixer.music.play(self.loop)
        return os.path.basename(self.files[ind])
    
    def note_off(self, channel, octave):
        """Stop playing the mp3 when the key is no longer pressed.
        """
        # NOTE: This stops all music from playing.
        #       need to figure out how to stop each individually.
        pygame.mixer.music.stop()


class Synth8Bit(Instrument):
    """Example 8-bit synthesizer
    
    Based on code from:
    https://github.com/pimoroni/Piano-HAT/blob/master/examples/8bit-synth.py
    
    The sine is still off and this needs more work.
    """
    def __init__(self):
        self.name = "8BitSynth"
        self.octaves=0
        self.initial_octave=0
        self.enabled = FlipFlopState()
        self.t2c = dict(sine=12, square=13, saw=14)
        
        self.BITRATE = 8
        self.SAMPLERATE = 44100
        self.ATTACK_MS=25
        self.RELEASE_MS=500
        
        self.stereo = True # need to ask pygame if it is stereo device (not just what we set)
        self.volume = {'sine':.15, 'saw':0.15, 'square':1.0}
        self.wavetypes = ['sine','saw','square']
        self.notes = {'sine':[],'saw':[],'square':[]}
        
        max_sample = (2**(self.BITRATE - 1)) - 1
        
        def wave_sine(freq, time):
            """Generates a single sine wave sample"""
            s = numpy.sin(2*numpy.pi*freq*time)
            return int(round(max_sample * s ))
        
        
        def wave_square(freq, time):
            """Generates a single square wave sample"""
            m = int(max_sample * 0.9)
            return -m if freq*time < 0.5 else m
        
        
        def wave_saw(freq, time):
            """Generates a single sav wave sample"""
            s = ((freq*time)*2) - 1
            return int(round(max_sample * s))
        
        def generate_sample(frequency, volume=1.0, wavetype=None, stereo=True):
            """Generates a sample of a specific frequency and wavetype"""
            if wavetype is None:
                wavetype = wave_square
        
            sample_count = int(round(self.SAMPLERATE/frequency))
        
            buf = numpy.array([wavetype(frequency, float(x)/self.SAMPLERATE)
                               for x in range(sample_count)]).astype(numpy.int8)
            if stereo:
                stbuf = numpy.zeros((sample_count, 2), dtype = numpy.int8)
                for i, b in enumerate(buf):
                    stbuf[i,0] = b
                    stbuf[i,1] = b
                buf = stbuf
            sound = pygame.sndarray.make_sound(buf)
        
            sound.set_volume(volume) # Set the volume to balance sounds
            return sound
        
        for f in [261.626,
                  277.183,
                  293.665,
                  311.127,
                  329.628,
                  349.228,
                  369.994,
                  391.995,
                  415.305,
                  440.000,
                  466.164,
                  493.883,
                  523.251]:
            self.notes['sine'] += [generate_sample(f, volume=self.volume['sine'],
                                                   wavetype=wave_sine)]
            self.notes['saw'] += [generate_sample(f, volume=self.volume['saw'],
                                                  wavetype=wave_saw)]
            self.notes['square'] += [generate_sample(f, volume=self.volume['square'],
                                                     wavetype=wave_square)]
        
    def toggle(self, t):
        """Toggle the on off state of the sine,square, or saw
        """
        self.t2c[t]
        en = self.enabled.toggle(t)
        ns = ' on' if en else ' off'
        if pianohat:
            pianohat.set_led(self.t2c[t], en)
        return t + ns
    
    def octave_up(self, octave):
        """Toggle saw"""
        return self.toggle('saw')
    
    def octave_down(self, octave):
        """Toggle square"""
        return self.toggle('square')
    
    def select(self):
        if pianohat:
            pianohat.auto_leds(False)
            for i in xrange(16):
                pianohat.set_led(i, False)
        self.enabled = FlipFlopState()
        pygame.mixer.pre_init(self.SAMPLERATE, -self.BITRATE, 1, 1024)
        pygame.mixer.init(channels=1)
        #pygame.mixer.set_num_channels(32)
        self.toggle('sine') # by default enable sine.
        return "C2=sine,v=square,^saw"
    
    def deselect(self):
        if pianohat:
            for i in xrange(16):
                pianohat.set_led(i, False)
            pianohat.auto_leds(True)
        self.enabled = FlipFlopState()
        pygame.mixer.stop()
        pygame.mixer.quit()

    def note_on(self, channel, octave):
        """Generate the soundwave
        """
        if channel == 12:
            return self.toggle('sine')
        if pianohat:
            pianohat.set_led(channel, True)
        note_name = key_name(channel)
        for t in self.wavetypes:
            if self.enabled[t]:
                note_name += ' ' + t
                self.notes[t][channel].play(-1, fade_ms=self.ATTACK_MS)
        return note_name
                
    def note_off(self, channel, octave):
        """Fade the key's soundwave to nothing
        """
        if channel == 12:
            return
        if pianohat:
            pianohat.set_led(channel, False)
        for t in self.wavetypes:
            self.notes[t][channel].fadeout(self.RELEASE_MS)




class Midi(Instrument):
    def __init__(self, client, name="", octaves=10, initial_octave=5,
                 port=0, patch=1, banks=16, velocity=100):
        self.client = client
        self.port = port
        self.patch = patch
        self.banks = banks
        self.name = "MIDI:"+name
        self.octaves = octaves
        self.initial_octave = initial_octave
        self.velocity = velocity
        
        self.seq = None
        
    def select_patch(self, patch):
        if patch < 0 or patch >= self.banks:
            return
        self.patch = patch
        self.seq.event_write(midi.ProgramChangeEvent(tick=0, channel=0, data=[patch]), False, False, True)
    
    def note_on(self, channel, octave):
        note = (octave * 12)  + channel
        self.seq.event_write(midi.NoteOnEvent(velocity=self.velocity, pitch=note, tick=0), False, False, True)
        return "on {}".format(note)
    
    def note_off(self, channel, octave):
        note = (octave * 12)  + channel
        self.seq.event_write(midi.NoteOffEvent(velocity=100, pitch=note, tick=0), False, False, True)
        return "off {}".format(note)
    
    def select(self):
        self.seq = midi.sequencer.SequencerWrite()
        self.seq.subscribe_port(self.client, self.port)
        self.seq.start_sequencer()
        self.select_patch(self.patch)
        
    def deselect(self):
        seq = self.seq
        self.seq.stop_sequencer()
        self.seq = None
        del seq

_MIDI_IGNORE = [
    '__sequencer__',
    'System',
    'Midi Through'
]
_MIDI_SUPPORTED = [
    'yoshimi',
    'SunVox',
    'CH345'
]
def load_midi_sequencers(phat, load_unknown=True):
    """Iterate over the available midi hardware, excluding known
    incompatable sequencers, and loadking known good ones.
    """
    if not midi:
        return
    hw = midi.sequencer.SequencerHardware()
    for name in hw._clients:
        if name in _MIDI_IGNORE:
            continue
        if name not in _MIDI_SUPPORTED:
            if not load_unknown:
                continue
            print("Loading unknown MIDI Hardware: "+name)
        phat.add_instrument(Midi(hw._clients[name].client, name))

def load_wav_instruments(phat, base_folder):
    """Load each sub directory of a given ``base_folder`` as a` ``WavPlayer``
    or ``Songs`` Instrument depending on what files are found, and add that
    Instrument to the supplied ``PiPianoUI``.
    """
    wavinstdirs = glob.glob(os.path.join(base_folder, '*'))
    for d in wavinstdirs:
        if os.path.isdir(d):
            inst = WavPlayer(d)
            if inst.files:
                phat.add_instrument(inst)
            inst = Songs(d)
            if inst.files:
                phat.add_instrument(inst)


_QUIT_KEYS = [
    pygame.locals.K_q,
    pygame.locals.K_ESCAPE
]

_KEYMAP = [
    pygame.locals.K_z,
    pygame.locals.K_s,
    pygame.locals.K_x,
    pygame.locals.K_d,
    pygame.locals.K_c,
    pygame.locals.K_v,
    pygame.locals.K_g,
    pygame.locals.K_b,
    pygame.locals.K_h,
    pygame.locals.K_n,
    pygame.locals.K_j,
    pygame.locals.K_m,
    pygame.locals.K_COMMA,
    pygame.locals.K_l,
    pygame.locals.K_o,
    pygame.locals.K_i,
] + _QUIT_KEYS



def main():
    """Example program to load the sample instruments and run the UI.
    
    Will work on a computer or RaspberryPi w/o a PianoHAT for testing using
    the keyboard.
    
    Keyboard mappings:
    
        * z-<comma> are mapped to the keys.
        * o/l are octave up and down
        * i is instrument
        * q/<esc> quit
    """
    print """Keyboard also works:
    
    * z-<comma> are mapped to the keys.
    * o/l are octave up and down
    * i is instrument
    * q/<esc> quit
    """
    # NOTE: Some of this logic should be moved into PiPianoUI
    #       Specifically the main loop.
    #       But that makes some customizations to keyboard/mouse control
    #       very difficult.
    p = PiPianoUI()
    p.add_instrument(Synth8Bit())
    load_wav_instruments(p, os.path.join(os.path.dirname(__file__), 'sounds'))
    load_midi_sequencers(p)
    p.message("{} insturments. q/<esc> to quit.".format(len(p.instruments)))
    quit = False
    while not quit:
        event = pygame.event.wait()
        if event.type == pygame.locals.QUIT:
            quit = True
        elif event.type not in [pygame.locals.KEYDOWN, pygame.locals.KEYUP]:
            continue
        if event.key in _QUIT_KEYS:
            quit = True
        else:
            ## Keyboard controls
            try:
                channel = _KEYMAP.index(event.key)
            except ValueError:
                channel = -1
            if channel > 15:
                quit = True
            elif channel < 13:
                p.handle_note(channel, event.type == pygame.locals.KEYDOWN)
            elif channel == 13:
                p.handle_octave_down(channel, event.type == pygame.locals.KEYDOWN)
            elif channel == 14:
                p.handle_octave_up(channel, event.type == pygame.locals.KEYDOWN)
            elif channel == 15:
                p.handle_instrument(channel, event.type == pygame.locals.KEYDOWN)
        pygame.display.update()
    pygame.quit()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
