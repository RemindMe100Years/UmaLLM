from jamdict import Jamdict

j = Jamdict()
r = j.lookup("hello")
meanings = [g.text for e in r.entries for s in e.senses for g in s.gloss]
print("Jamdict OK: hello/こんにちは ->", ", ".join(meanings[:3]))
