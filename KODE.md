KODE.md

So several issues here, first, something must be done to keep things connected during run. Also, we ought to (just before startup) make sure old process is cleared and the port range is cleared. The project, both PC and Android sides, should run regardless of any issues the other is having. There should be a single way to start the project (implemented). I'm almost thinking we could "pair" the Android to the PC to simplify any IP or port debacles that could arise. When it has a critical failure, the entire program, both sides, must gracefully shutdown and log a crash report for  details. Speaking of logs, PLEASE do not simply PASS exceptions, that way I don't have to get cranky at 0215. Watch out with creating files and name drift, don't assume either way if something is there... look, check if it is to see if the function you are working on is called something else... it isn't happening often, but it does. Please verify your work that it works as intended and looks and feels like it belongs, ask if not sure. Please don't overcomplicate your reasoning, instincts are only instincts until you put thought processing to them. srp theme pack exists, use it along with icons and branding (srp = Slutty Rabbit Productions). Thank you for being a part of it.

Moving along with issues we are facing: we've covered the startup mostly. Now every slider on the interface has a purpose, purposes need functions. If the slider is not working, write the function and make it work. That is ALL sliders, either side where applicable. When verifying, make sure it is 100% good to go, placeholders are something I am not a fan of UNLESS NECESSARY such as a default value that MUST have a value to run. The other exception is secrets. I believe in configurability as much as possible to ensure flexibility. Do not hard code configurable values unless mandatory, so NO PLACEHOLDER for future code, and snippets are of no existent use here, we build for tomorrow, today.

So... verification thoroughness. I notice some issues with dB or RMS meter locations on the UI. By the way, my name is not "the user", it is Jason or Dani, you choose.

Moving on, single source of truth is no joke, it keeps things real and tight and honest. Logging is no exception. 

PLEASE for fucks sake, remember what this in being built on: a slow ass 4 core 16 Gib ram cpu only mini pc running ubuntu. it is more powerful than most people realize, but that doesnt mean slam it like it can take iy. iv actually destroyed a couple of these just pushing them too hard. so make the decisions count where the count matters, such as on the wgui... the entire fuckingdb doesnt need to be loaded at once! shit like that is unnecessar and completely avoidable. AGAIN watch name drift and stop duplicates. this is still in its peborn stages not even infantcy. that said, COMPLETE PASSDOWN.md and CHANGELOG.me on regular basis  . in STRUCT.md keep all system namings, paths, files,  refrences, truths up to date so if an issue arises it cam be compaired and easilt fixed.  
  
  important*** both of the graphs in the web gui (wave and spectrogram) each need their own toggle to enable/disable manually.
   - before the extract function runs via button press in gui, both of the graphs must be disabled untin it completes.
   -models should be called, loaded, and unloaded dynamically as to save resources. it will be slower, but as it is we simply crash 2 mins in.

  ***removed contingancy***

     Remember, this project was contingent on a latency <300ms, so the UI graphs are looking wonderful. However, they are on the choppy side for smoothness in scrolling. I think this is more than enough for now.





hank you dearly,
srp
