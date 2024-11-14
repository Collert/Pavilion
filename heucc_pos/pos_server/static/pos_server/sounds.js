export function playBeep() {
    // Create an audio context
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  
    // Create an oscillator (the sound source)
    const oscillator = audioCtx.createOscillator();
    oscillator.type = "sine"; // Use a sine wave for a basic beep sound
    oscillator.frequency.setValueAtTime(440, audioCtx.currentTime); // Set frequency to 440 Hz (A4 note)
  
    // Create a gain node (controls the volume)
    const gainNode = audioCtx.createGain();
    gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime); // Set volume to a low level
  
    // Connect oscillator -> gain -> audio context's output (speakers)
    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);
  
    // Set the duration of the beep
    oscillator.start();
    oscillator.stop(audioCtx.currentTime + 0.2); // Beep lasts 0.2 seconds
}
  