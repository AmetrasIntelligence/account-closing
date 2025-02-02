# Copyright 2013 Akretion (http://www.akretion.com/)
# Copyright 2018 Jacques-Etienne Baudoux (BCIM) <je@bcim.be>
# @author: Alexis de Lattre <alexis.delattre@akretion.com>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import json
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import date_utils, float_is_zero
from odoo.tools.misc import format_date


class PosNeg:
    def __init__(self, amount):
        self.amount_pos = self.amount_neg = 0
        self.__iadd__(amount)

    def __iadd__(self, amount):
        if amount < 0:
            self.amount_neg += amount
        else:
            self.amount_pos += amount
        return self


class AccountCutoff(models.Model):
    _name = "account.cutoff"
    _rec_name = "cutoff_date"
    _order = "cutoff_date desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _check_company_auto = True
    _description = "Account Cut-off"

    @api.depends("line_ids", "line_ids.cutoff_amount")
    def _compute_total_cutoff(self):
        rg_res = self.env["account.cutoff.line"].read_group(
            [("parent_id", "in", self.ids)],
            ["parent_id", "cutoff_amount"],
            ["parent_id"],
        )
        mapped_data = {x["parent_id"][0]: x["cutoff_amount"] for x in rg_res}
        for cutoff in self:
            cutoff.total_cutoff_amount = mapped_data.get(cutoff.id, 0)

    @property
    def cutoff_type_label_map(self):
        return {
            "accrued_expense": _("Accrued Expense"),
            "accrued_revenue": _("Accrued Revenue"),
            "prepaid_revenue": _("Prepaid Revenue"),
            "prepaid_expense": _("Prepaid Expense"),
        }

    @api.model
    def _default_move_ref(self):
        cutoff_type = self.env.context.get("default_cutoff_type")
        ref = self.cutoff_type_label_map.get(cutoff_type, "")
        return ref

    @api.model
    def _default_cutoff_date(self):
        today = fields.Date.context_today(self)
        company = self.env.company
        date_from, date_to = date_utils.get_fiscal_year(
            today,
            day=company.fiscalyear_last_day,
            month=int(company.fiscalyear_last_month),
        )
        if date_from:
            return date_from - relativedelta(days=1)
        else:
            return False

    def _selection_cutoff_type(self):
        # generate cutoff types from mapping
        return list(self.cutoff_type_label_map.items())

    @api.model
    def _default_cutoff_account_id(self):
        cutoff_type = self.env.context.get("default_cutoff_type")
        company = self.env.company
        if cutoff_type == "accrued_expense":
            account_id = company.default_accrued_expense_account_id.id or False
        elif cutoff_type == "accrued_revenue":
            account_id = company.default_accrued_revenue_account_id.id or False
        elif cutoff_type == "prepaid_revenue":
            account_id = company.default_prepaid_revenue_account_id.id or False
        elif cutoff_type == "prepaid_expense":
            account_id = company.default_prepaid_expense_account_id.id or False
        else:
            account_id = False
        return account_id

    cutoff_date = fields.Date(
        string="Cut-off Date",
        states={"done": [("readonly", True)]},
        copy=False,
        tracking=True,
        default=lambda self: self._default_cutoff_date(),
    )
    cutoff_type = fields.Selection(
        selection="_selection_cutoff_type",
        string="Type",
        required=True,
        states={"done": [("readonly", True)]},
    )
    source_move_state = fields.Selection(
        [("posted", "Posted Entries"), ("draft_posted", "Draft and Posted Entries")],
        string="Source Entries",
        required=True,
        default="posted",
        states={"done": [("readonly", True)]},
        tracking=True,
    )
    move_id = fields.Many2one(
        "account.move",
        string="Cut-off Journal Entry",
        readonly=True,
        copy=False,
        check_company=True,
    )
    auto_reverse = fields.Boolean(
        help="Automatically reverse created move on following day. Use this "
        "if you accrue a value end of period that you want to reverse "
        "begin of next period",
    )
    move_reversal_id = fields.Many2one(
        "account.move",
        string="Cut-off Journal Entry Reversal",
        compute="_compute_move_reversal_id",
    )

    @api.depends("move_id.reversal_move_id", "move_id.reversal_move_id.state")
    def _compute_move_reversal_id(self):
        for rec in self:
            rec.move_reversal_id = rec.move_id.reversal_move_id.filtered(
                lambda m: m.state != "cancel"
            )[:1]

    move_ref = fields.Char(
        string="Reference of the Cut-off Journal Entry",
        states={"done": [("readonly", True)]},
        default=lambda self: self._default_move_ref(),
    )
    move_partner = fields.Boolean(
        string="Partner on Journal Items",
        default=lambda self: self.env.company.default_cutoff_move_partner,
        states={"done": [("readonly", True)]},
        tracking=True,
    )
    cutoff_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Cut-off Account",
        domain="[('deprecated', '=', False), ('company_id', '=', company_id)]",
        states={"done": [("readonly", True)]},
        default=lambda self: self._default_cutoff_account_id(),
        check_company=True,
        tracking=True,
    )
    cutoff_journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Cut-off Account Journal",
        default=lambda self: self.env.company.default_cutoff_journal_id,
        states={"done": [("readonly", True)]},
        domain="[('company_id', '=', company_id)]",
        check_company=True,
        tracking=True,
    )
    total_cutoff_amount = fields.Monetary(
        compute="_compute_total_cutoff",
        string="Total Cut-off Amount",
        currency_field="company_currency_id",
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        states={"done": [("readonly", True)]},
        default=lambda self: self.env.company,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id", string="Company Currency"
    )
    line_ids = fields.One2many(
        comodel_name="account.cutoff.line",
        inverse_name="parent_id",
        string="Cut-off Lines",
        states={"done": [("readonly", True)]},
    )
    state = fields.Selection(
        selection=[("draft", "Draft"), ("done", "Done")],
        index=True,
        readonly=True,
        tracking=True,
        default="draft",
        copy=False,
        help="State of the cutoff. When the Journal Entry is created, "
        "the state is set to 'Done' and the fields become read-only.",
    )

    _sql_constraints = [
        (
            "date_type_company_uniq",
            "unique(cutoff_date, company_id, cutoff_type)",
            _("A cutoff of the same type already exists with this cut-off date !"),
        )
    ]

    def name_get(self):
        res = []
        type2label = self.cutoff_type_label_map
        for rec in self:
            name = type2label.get(rec.cutoff_type, "")
            if rec.cutoff_date:
                name = "%s %s" % (name, format_date(self.env, rec.cutoff_date))
            res.append((rec.id, name))
        return res

    def back2draft(self):
        self.ensure_one()
        if self.move_reversal_id:
            self.move_reversal_id.line_ids.remove_move_reconcile()
            self.move_reversal_id.unlink()
            self.move_id.line_ids.remove_move_reconcile()
        if self.move_id:
            self.move_id.unlink()
        self.write({"state": "draft"})

    def _get_merge_keys(self):
        """Return merge criteria for provision lines

        The returned list must contain valid field names
        for account.move.line. Provision lines with the
        same values for these fields will be merged.
        The list must at least contain account_id.
        """
        return ["partner_id", "account_id", "analytic_distribution"]

    def _prepare_move(self, to_provision):
        self.ensure_one()
        movelines_to_create = []
        amount_total_pos = 0
        amount_total_neg = 0
        ref = self.move_ref
        company_currency = self.company_id.currency_id
        cur_rprec = company_currency.rounding
        merge_keys = self._get_merge_keys()
        for merge_values, amount in to_provision.items():
            amount_total_neg += self.company_currency_id.round(amount.amount_neg)
            amount_total_pos += self.company_currency_id.round(amount.amount_pos)
            amount = amount.amount_pos + amount.amount_neg
            if float_is_zero(amount, precision_rounding=cur_rprec):
                continue
            vals = {
                "debit": amount < 0 and amount * -1 or 0,
                "credit": amount >= 0 and amount or 0,
                "tax_ids": False,  # neutralize defaut tax of account
            }
            for k, v in zip(merge_keys, merge_values):
                value = v
                if k == "analytic_distribution" and isinstance(v, str):
                    value = json.loads(value)

                vals[k] = value

            movelines_to_create.append((0, 0, vals))

        # add counter-part
        movelines_to_create += self._prepare_counterpart_moves(
            to_provision, amount_total_pos, amount_total_neg
        )

        res = {
            "company_id": self.company_id.id,
            "journal_id": self.cutoff_journal_id.id,
            "date": self.cutoff_date,
            "ref": ref,
            "line_ids": movelines_to_create,
        }
        return res

    def _prepare_counterpart_moves(
        self, to_provision, amount_total_pos, amount_total_neg
    ):
        amount = (amount_total_pos + amount_total_neg) * -1
        company_currency = self.company_id.currency_id
        cur_rprec = company_currency.rounding
        if float_is_zero(amount, precision_rounding=cur_rprec):
            return []
        return [
            (
                0,
                0,
                {
                    "account_id": self.cutoff_account_id.id,
                    "debit": amount < 0 and amount * -1 or 0,
                    "credit": amount >= 0 and amount or 0,
                },
            )
        ]

    def _prepare_provision_line(self, cutoff_line):
        """Convert a cutoff line to elements of a move line.

        The returned dictionary must at least contain 'account_id'
        and 'amount' (< 0 means debit).

        If you override this, the added fields must also be
        added in an override of _get_merge_keys.
        """
        partner_id = cutoff_line.partner_id.id or False
        return {
            "partner_id": self.move_partner and partner_id or False,
            "account_id": cutoff_line.cutoff_account_id.id,
            "analytic_distribution": cutoff_line.analytic_distribution,
            "amount": cutoff_line.cutoff_amount,
        }

    def _prepare_provision_tax_line(self, cutoff_tax_line):
        """Convert a cutoff tax line to elements of a move line.

        See _prepare_provision_line for more info.
        """
        return {
            "partner_id": False,
            "account_id": cutoff_tax_line.cutoff_account_id.id,
            "analytic_distribution": cutoff_tax_line.analytic_distribution,
            "amount": cutoff_tax_line.cutoff_amount,
        }

    def _merge_provision_lines(self, provision_lines):
        """Merge provision line.

        Returns a dictionary {key, amount} where key is
        a tuple containing the values of the properties in _get_merge_keys()
        """
        to_provision = defaultdict(float)
        merge_keys = self._get_merge_keys()
        for provision_line in provision_lines:
            key = tuple(
                isinstance(provision_line.get(key), dict)
                and json.dumps(provision_line.get(key))
                or provision_line.get(key)
                for key in merge_keys
            )
            if key in to_provision:
                to_provision[key] += provision_line["amount"]
            else:
                to_provision[key] = PosNeg(provision_line["amount"])
        return to_provision

    def create_move(self):
        self.ensure_one()
        move_obj = self.env["account.move"]
        if self.move_id:
            raise UserError(
                _(
                    "The Cut-off Journal Entry already exists. You should "
                    "delete it before running this function."
                )
            )
        if not self.line_ids:
            raise UserError(
                _(
                    "There are no lines on this Cut-off, so we can't create "
                    "a Journal Entry."
                )
            )
        provision_lines = []
        for line in self.line_ids:
            provision_lines.append(self._prepare_provision_line(line))
            for tax_line in line.tax_line_ids:
                provision_lines.append(self._prepare_provision_tax_line(tax_line))
        to_provision = self._merge_provision_lines(provision_lines)
        vals = self._prepare_move(to_provision)
        move = move_obj.create(vals)
        if self.company_id.post_cutoff_move:
            move._post(soft=False)

        if self.auto_reverse:
            next_day = fields.Date.from_string(self.cutoff_date) + relativedelta(days=1)
            rev_move = move._reverse_moves(
                [
                    {
                        "date": next_day,
                        "ref": _("reversal of: ") + move.ref,
                    }
                ]
            )
            if self.company_id.post_cutoff_move:
                rev_move._post(soft=False)

        self.write({"move_id": move.id, "state": "done"})
        self.message_post(body=_("Journal entry generated"))

        action = self.env.ref("account.action_move_journal_line").sudo().read()[0]
        action.update(
            {
                "view_mode": "form,tree",
                "res_id": move.id,
                "view_id": False,
                "views": False,
            }
        )
        return action

    def get_lines(self):
        """This method is designed to be inherited in other modules"""
        self.ensure_one()
        assert self.state != "done"
        # I test self.state == 'draft' below because other modules
        # (e.g. account_cutoff_start_end_dates) add additional states
        # and don't require self.cutoff_date
        if self.state == "draft" and not self.cutoff_date:
            raise UserError(_("Cutoff date is not set."))
        # Delete existing lines
        self.line_ids.unlink()
        self.message_post(body=_("Cut-off lines re-generated"))

    def unlink(self):
        for rec in self:
            if rec.state == "done":
                raise UserError(
                    _("You cannot delete cutoff records that are in done state.")
                )
        return super().unlink()

    def button_line_tree(self):
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account_cutoff_base.account_cutoff_line_action"
        )
        action.update(
            {
                "domain": [("parent_id", "=", self.id)],
                "views": False,
            }
        )
        return action

    def _get_mapping_dict(self):
        """return a dict with:
        key = ID of account,
        value = ID of cutoff_account"""
        self.ensure_one()
        mappings = self.env["account.cutoff.mapping"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("cutoff_type", "in", ("all", self.cutoff_type)),
            ]
        )
        mapping = {}
        for item in mappings:
            mapping[item.account_id.id] = item.cutoff_account_id.id
        return mapping

    def _prepare_tax_lines(self, tax_compute_all_res, currency):
        res = []
        ato = self.env["account.tax"]
        company_currency = self.company_id.currency_id
        cur_rprec = company_currency.rounding
        for tax_line in tax_compute_all_res["taxes"]:
            tax = ato.browse(tax_line["id"])
            if float_is_zero(tax_line["amount"], precision_rounding=cur_rprec):
                continue

            tax_accrual_account = False
            tax_account_field_label = ""
            if self.cutoff_type == "accrued_expense":
                tax_accrual_account = (
                    tax.account_accrued_expense_id
                    or self.company_id.default_accrued_expense_tax_account_id
                )
                tax_account_field_label = _("Accrued Expense Tax Account")
            elif self.cutoff_type == "accrued_revenue":
                tax_accrual_account = (
                    tax.account_accrued_revenue_id
                    or self.company_id.default_accrued_revenue_tax_account_id
                )
                tax_account_field_label = _("Accrued Revenue Tax Account")

            if not tax_accrual_account:
                raise UserError(
                    _(
                        "Missing '%(tax_account_field_label)s'. You must configure it "
                        "on the tax '%(tax_display_name)s' or on the accounting "
                        "configuration page of the company '%(company)s'.",
                        tax_account_field_label=tax_account_field_label,
                        tax_display_name=tax.display_name,
                        company=self.company_id.display_name,
                    )
                )
            tax_amount = currency.round(tax_line["amount"])
            tax_accrual_amount = currency._convert(
                tax_amount, company_currency, self.company_id, self.cutoff_date
            )
            res.append(
                (
                    0,
                    0,
                    {
                        "tax_id": tax_line["id"],
                        "base": tax_line["base"],  # in currency
                        "amount": tax_amount,  # in currency
                        "sequence": tax_line["sequence"],
                        "cutoff_account_id": tax_accrual_account.id,
                        "cutoff_amount": tax_accrual_amount,  # in company currency
                    },
                )
            )
        return res
