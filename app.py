import streamlit as st
import sqlite3
from collections import defaultdict
import pandas as pd

# ================= HELPER =================
def rerun_app():
    """Safely rerun the Streamlit app across Streamlit versions."""
    try:
        st.experimental_rerun()
    except AttributeError:
        st.stop()  # stops execution and refreshes on next interaction

# ================= DB SETUP =================
conn = sqlite3.connect("expenses_final.db", check_same_thread=False)
c = conn.cursor()

# Users table
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
)
""")

# Groups table
c.execute("""
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
)
""")

# Group members table
c.execute("""
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER,
    user_id INTEGER,
    FOREIGN KEY(group_id) REFERENCES groups(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
)
""")

# Expenses table
c.execute("""
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    payer_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    group_id INTEGER NOT NULL,
    FOREIGN KEY(payer_id) REFERENCES users(id),
    FOREIGN KEY(group_id) REFERENCES groups(id)
)
""")

# Expense participants table
c.execute("""
CREATE TABLE IF NOT EXISTS expense_participants (
    expense_id INTEGER,
    user_id INTEGER,
    share REAL,
    FOREIGN KEY(expense_id) REFERENCES expenses(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
)
""")

# Payments table
c.execute("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    payer_id INTEGER NOT NULL,
    payee_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    description TEXT,
    FOREIGN KEY(group_id) REFERENCES groups(id),
    FOREIGN KEY(payer_id) REFERENCES users(id),
    FOREIGN KEY(payee_id) REFERENCES users(id)
)
""")
conn.commit()

# ================= HELPERS =================
def get_user_id(name):
    name = name.strip()
    c.execute("SELECT id FROM users WHERE name = ?", (name,))
    row = c.fetchone()
    if row:
        return row[0]
    c.execute("INSERT INTO users (name) VALUES (?)", (name,))
    conn.commit()
    return c.lastrowid

def get_group_id(name):
    name = name.strip()
    c.execute("SELECT id FROM groups WHERE name = ?", (name,))
    row = c.fetchone()
    if row:
        return row[0]
    c.execute("INSERT INTO groups (name) VALUES (?)", (name,))
    conn.commit()
    return c.lastrowid

def get_group_members(group_id):
    c.execute("SELECT user_id FROM group_members WHERE group_id = ?", (group_id,))
    return [r[0] for r in c.fetchall()]

def compute_settlements(group_id):
    debts = defaultdict(lambda: defaultdict(float))
    per_expense_readable = []

    # Fetch expenses for this group
    c.execute("SELECT id, amount, payer_id, description FROM expenses WHERE group_id = ?", (group_id,))
    expenses = c.fetchall()

    for expense_id, amount, payer_id, desc in expenses:
        c.execute("SELECT user_id, share FROM expense_participants WHERE expense_id = ?", (expense_id,))
        participants = c.fetchall()
        for uid, share in participants:
            if uid != payer_id:
                debts[uid][payer_id] += share
                per_expense_readable.append((uid, payer_id, share, desc))

    # Fetch recorded payments and subtract them
    c.execute("SELECT payer_id, payee_id, amount FROM payments WHERE group_id = ?", (group_id,))
    payments = c.fetchall()
    for payer_id, payee_id, amount in payments:
        debts[payer_id][payee_id] -= amount  # subtract payment
        if debts[payer_id][payee_id] < 0:   # reverse debt if overpaid
            debts[payee_id][payer_id] = -debts[payer_id][payee_id]
            debts[payer_id][payee_id] = 0

    # Map user IDs to names
    c.execute("SELECT id, name FROM users")
    id_to_name = dict(c.fetchall())

    # Pairwise net settlements — safe iteration
    final_settlements = []
    processed_pairs = set()
    for u in list(debts.keys()):
        for v in list(debts[u].keys()):
            if (v, u) in processed_pairs:
                continue
            net_amt = debts[u][v] - debts[v].get(u, 0)
            if net_amt > 0:
                final_settlements.append((id_to_name[u], id_to_name[v], net_amt))
            elif net_amt < 0:
                final_settlements.append((id_to_name[v], id_to_name[u], -net_amt))
            processed_pairs.add((u, v))

    # Per-expense readable
    per_expense_text = [
        f"{id_to_name[uid]} owes {id_to_name[payer_id]} ₹{share:,.2f} for '{desc}'"
        for uid, payer_id, share, desc in per_expense_readable
    ]

    # Compute net balance per participant
    net_balance = defaultdict(float)
    for u, v, amt in final_settlements:
        net_balance[u] -= amt
        net_balance[v] += amt

    return final_settlements, per_expense_text, net_balance

# ================= UI =================
st.title("Better Splitwise")
st.sidebar.subheader("Groups & Users")

# Add group
new_group = st.sidebar.text_input("New Group Name")
if st.sidebar.button("Add Group") and new_group:
    get_group_id(new_group)
    st.sidebar.success(f"Group '{new_group}' added!")
    rerun_app()

# Select group
c.execute("SELECT id, name FROM groups")
groups = c.fetchall()
group_options = {name: gid for gid, name in groups}
selected_group_name = st.sidebar.selectbox("Select Group", list(group_options.keys()) if group_options else [])
selected_group_id = group_options[selected_group_name] if selected_group_name else None

if selected_group_id:
    st.sidebar.markdown("**Add Members**")
    new_member = st.sidebar.text_input("New Member Name")
    if st.sidebar.button("Add Member") and new_member:
        user_id = get_user_id(new_member)
        if user_id not in get_group_members(selected_group_id):
            c.execute("INSERT INTO group_members VALUES (?, ?)", (selected_group_id, user_id))
            conn.commit()
            st.sidebar.success(f"{new_member} added to {selected_group_name}!")
            rerun_app()

    # ================= DELETE GROUP =================
    st.sidebar.markdown("### Delete Group")
    if st.sidebar.button(f"Delete Group '{selected_group_name}'"):
        # Delete expense participants
        c.execute("""
            DELETE FROM expense_participants
            WHERE expense_id IN (SELECT id FROM expenses WHERE group_id = ?)
        """, (selected_group_id,))
        # Delete expenses
        c.execute("DELETE FROM expenses WHERE group_id = ?", (selected_group_id,))
        # Delete payments
        c.execute("DELETE FROM payments WHERE group_id = ?", (selected_group_id,))
        # Delete group members
        c.execute("DELETE FROM group_members WHERE group_id = ?", (selected_group_id,))
        # Delete the group itself
        c.execute("DELETE FROM groups WHERE id = ?", (selected_group_id,))
        conn.commit()
        st.sidebar.success(f"Group '{selected_group_name}' deleted!")
        rerun_app()

# ================= ADD EXPENSE =================
if selected_group_id:
    st.subheader(f"Add Expense to '{selected_group_name}'")
    desc = st.text_input("Expense description", key="desc")
    payer = st.text_input("Paid by", key="payer")
    amount = st.number_input("Amount", min_value=0.0, step=1.0, key="amount")
    split_type = st.selectbox("Split type", ["Equal", "Custom", "Percentage"])
    members = get_group_members(selected_group_id)
    c.execute("SELECT id, name FROM users WHERE id IN (%s)" % ",".join(str(m) for m in members))
    group_member_names = [r[1] for r in c.fetchall()]
    participants_input = st.multiselect("Select Participants", group_member_names)

    custom_shares = {}
    if split_type in ["Custom", "Percentage"] and participants_input:
        st.write("Specify shares:")
        for p in participants_input:
            val = st.number_input(f"{p}'s share", min_value=0.0, value=0.0, key=f"share_{p}")
            custom_shares[p] = val

    if st.button("Add Expense"):
        if not desc or not payer or amount <= 0 or not participants_input:
            st.error("All fields are required.")
        elif payer not in participants_input:
            st.error("Payer must be in participants.")
        elif len(participants_input) != len(set(participants_input)):
            st.error("No duplicate participants allowed.")
        else:
            payer_id = get_user_id(payer)
            participant_ids = [get_user_id(p) for p in participants_input]

            # Determine shares
            shares = []
            if split_type == "Equal":
                share_val = amount / len(participant_ids)
                shares = [share_val] * len(participant_ids)
            elif split_type == "Custom":
                shares = [custom_shares[p] for p in participants_input]
            elif split_type == "Percentage":
                total_pct = sum(custom_shares[p] for p in participants_input)
                if total_pct != 100:
                    st.error("Percentages must sum to 100")
                    st.stop()
                shares = [amount * custom_shares[p]/100 for p in participants_input]

            c.execute("INSERT INTO expenses (description, payer_id, amount, group_id) VALUES (?, ?, ?, ?)",
                      (desc, payer_id, amount, selected_group_id))
            expense_id = c.lastrowid

            for uid, share in zip(participant_ids, shares):
                c.execute("INSERT INTO expense_participants VALUES (?, ?, ?)", (expense_id, uid, share))
            conn.commit()
            st.success("Expense added!")
            rerun_app()

# ================= RECORD PAYMENTS =================
if selected_group_id:
    st.subheader("Record a Payment / Repayment")
    members = get_group_members(selected_group_id)
    c.execute("SELECT id, name FROM users WHERE id IN (%s)" % ",".join(str(m) for m in members))
    group_member_names = [r[1] for r in c.fetchall()]

    payer_payment = st.selectbox("Payer", group_member_names, key="pay_payer")
    payee_payment = st.selectbox("Payee", group_member_names, key="pay_payee")
    pay_amount = st.number_input("Amount Paid", min_value=0.0, step=1.0, key="pay_amount")
    pay_desc = st.text_input("Payment Note (optional)", key="pay_desc")

    if st.button("Record Payment"):
        if payer_payment == payee_payment:
            st.error("Payer and payee cannot be the same.")
        elif pay_amount <= 0:
            st.error("Amount must be positive.")
        else:
            payer_id = get_user_id(payer_payment)
            payee_id = get_user_id(payee_payment)
            c.execute("INSERT INTO payments (group_id, payer_id, payee_id, amount, description) VALUES (?, ?, ?, ?, ?)",
                      (selected_group_id, payer_id, payee_id, pay_amount, pay_desc))
            conn.commit()
            st.success("Payment recorded!")
            rerun_app()

# ================= DISPLAY =================
if selected_group_id:
    st.subheader(f"All Expenses in '{selected_group_name}'")
    c.execute("""
        SELECT e.id, e.description, u.name, e.amount
        FROM expenses e
        JOIN users u ON e.payer_id = u.id
        WHERE e.group_id = ?
    """, (selected_group_id,))
    expenses = c.fetchall()

    for eid, desc, payer_name, amt in expenses:
        c.execute("""
            SELECT u.name
            FROM expense_participants ep
            JOIN users u ON ep.user_id = u.id
            WHERE ep.expense_id = ?
        """, (eid,))
        parts = ", ".join([r[0] for r in c.fetchall()])

        col1, col2 = st.columns([5, 1])
        with col1:
            st.write(f"**{desc}** — {parts} owe **{payer_name}** ₹{amt:,.2f}")
        with col2:
            if st.button("❌", key=f"del_{eid}"):
                c.execute("DELETE FROM expense_participants WHERE expense_id = ?", (eid,))
                c.execute("DELETE FROM expenses WHERE id = ?", (eid,))
                conn.commit()
                rerun_app()

# ================= SETTLEMENTS =================
if selected_group_id:
    st.subheader(f"Settlements for '{selected_group_name}'")
    final_settlements, per_expense_text, net_balance = compute_settlements(selected_group_id)

    st.markdown("### Per-Expense Details")
    if per_expense_text:
        for s in per_expense_text:
            st.write(s)
    else:
        st.write("No per-expense debts yet.")

    st.markdown("### Per-Participant Summary")
    df = pd.DataFrame([
        {"Participant": user, "Net Balance (₹)": f"{bal:,.2f}"} for user, bal in net_balance.items()
    ])
    st.dataframe(df)

    st.markdown("### Final Settlements")
    if final_settlements:
        for u, v, amt in final_settlements:
            st.write(f"{u} pays {v} ₹{amt:,.2f}")
    else:
        st.write("All settled 🎉")
