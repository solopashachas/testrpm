document.querySelectorAll(".copy-button").forEach((button) => {
  button.addEventListener("click", async () => {
    const command = button.parentElement.querySelector("code").textContent;
    try {
      await navigator.clipboard.writeText(command);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    window.setTimeout(() => {
      button.textContent = "Copy commands";
    }, 2000);
  });
});
