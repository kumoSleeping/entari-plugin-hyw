
try:
    from arclet.entari import AccountConnect
    print("AccountConnect found in arclet.entari")
except ImportError:
    print("AccountConnect NOT found in arclet.entari")

try:
    from satori.event import LoginAdded
    print("LoginAdded found in satori.event")
except ImportError:
    print("LoginAdded NOT found in satori.event")
