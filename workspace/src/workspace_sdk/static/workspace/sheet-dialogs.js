export function initSheetDialogs({
  sheets, getActiveSheet, command, getWorkspace, removeSheet, selectSheet, showCurrentSheet, syncSheets,
}) {
  const newSheet = document.querySelector("#new-sheet");
  const createDialog = document.querySelector("#create-sheet");
  const createForm = createDialog.querySelector("form");
  const sheetTitle = createForm.querySelector("#sheet-title");
  const createError = createForm.querySelector("#create-error");
  const createButton = createForm.querySelector('button[type="submit"]');
  const cancelSheet = createForm.querySelector("#cancel-sheet");
  const deleteDialog = document.querySelector("#delete-sheet");
  const deleteForm = deleteDialog.querySelector("form");
  const deleteMessage = deleteForm.querySelector("#delete-sheet-message");
  const deleteError = deleteForm.querySelector(".dialog-error");
  const deleteButton = deleteForm.querySelector('button[type="submit"]');
  const cancelDelete = deleteForm.querySelector('button[type="button"]');
  let sheetToDelete = null;

  function confirmDelete(sheet) {
    sheetToDelete = sheet;
    deleteError.hidden = true;
    deleteMessage.textContent = `“${sheet.name}” will stop if running. Its Python files and history will move to .workspace/deleted-flows.`;
    deleteDialog.showModal();
  }

  cancelDelete.addEventListener("click", () => deleteDialog.close());
  deleteDialog.addEventListener("cancel", event => {
    if (deleteButton.disabled) event.preventDefault();
  });
  deleteForm.addEventListener("submit", async event => {
    event.preventDefault();
    deleteError.hidden = true;
    deleteButton.disabled = cancelDelete.disabled = true;
    try {
      await command(sheetToDelete, "delete");
      removeSheet(sheetToDelete.index);
      showCurrentSheet();
      deleteDialog.close();
      (getActiveSheet()?.tab ?? newSheet).focus();
    } catch (error) {
      deleteError.textContent = error.message;
      deleteError.hidden = false;
    } finally {
      deleteButton.disabled = cancelDelete.disabled = false;
    }
  });

  newSheet.addEventListener("click", () => {
    createForm.reset();
    createError.hidden = true;
    createDialog.showModal();
  });
  cancelSheet.addEventListener("click", () => createDialog.close());
  createDialog.addEventListener("cancel", event => {
    if (createButton.disabled) event.preventDefault();
  });
  createForm.addEventListener("submit", async event => {
    event.preventDefault();
    createError.hidden = true;
    createButton.disabled = cancelSheet.disabled = true;
    try {
      const response = await fetch("./flows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: sheetTitle.value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "Could not create the sheet.");
      syncSheets((await getWorkspace()).flows);
      const sheet = sheets[data.index];
      selectSheet(sheet);
      createDialog.close();
      sheet.tab.focus();
    } catch (error) {
      createError.textContent = error.message;
      createError.hidden = false;
      sheetTitle.focus();
    } finally {
      createButton.disabled = cancelSheet.disabled = false;
    }
  });

  return { confirmDelete };
}
