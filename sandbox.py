from gpiozero import Button
from signal import pause

hook = Button(
    17,
    pull_up=True,
    bounce_time=0.05
)

def off_hook():
    print("☎ handset lifted")

def on_hook():
    print("☎ handset replaced")

hook.when_pressed = off_hook
hook.when_released = on_hook

pause()