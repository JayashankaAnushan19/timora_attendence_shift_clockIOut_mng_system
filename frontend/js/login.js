(() => {
  const $ = (id) => document.getElementById(id);

  function showError(message) {
    $("error").textContent = message;
    $("error").hidden = !message;
  }

  function showChangeForm(currentPassword) {
    $("loginForm").hidden = true;
    $("changeForm").hidden = false;
    if (currentPassword) $("currentPassword").value = currentPassword;
    (currentPassword ? $("newPassword") : $("currentPassword")).focus();
  }

  $("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    const button = event.target.querySelector("button");
    button.disabled = true;
    const password = $("password").value;
    try {
      const data = await T.api("POST", "/api/auth/login", { username: $("username").value, password });
      if (data.admin.must_change_password) showChangeForm(password);
      else location.href = "/admin";
    } catch (err) {
      showError(err.message);
      $("password").select();
    } finally {
      button.disabled = false;
    }
  });

  $("changeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    showError("");
    if ($("newPassword").value !== $("repeatPassword").value) {
      showError("The new passwords do not match.");
      return;
    }
    const button = event.target.querySelector("button");
    button.disabled = true;
    try {
      await T.api("POST", "/api/auth/change-password", {
        current_password: $("currentPassword").value,
        new_password: $("newPassword").value,
      });
      location.href = "/admin";
    } catch (err) {
      showError(err.message);
    } finally {
      button.disabled = false;
    }
  });

  // Reloaded mid-way through a required change: go straight back to that form.
  T.api("GET", "/api/admin/me").then((data) => {
    if (data.admin.must_change_password) showChangeForm("");
  }).catch(() => {});
})();
