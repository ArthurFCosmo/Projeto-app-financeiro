"""Controller de Categorias e Orçamentos do FinançasSimples (docs/FSD.md - Seções 6.6, 12.7).

Implementa os endpoints da API REST para gestão completa de categorias:
- Listagem agrupada/filtrada com cálculo de consumo orçamentário do mês em tempo real.
- Criação com validação de unicidade de nome por usuário e tipo (receita/despesa).
- Edição de nome e teto orçamentário (para despesas).
- Arquivamento e reativação lógica.
- Exclusão física direta bloqueada se houver movimentações vinculadas.
- Fluxo de reatribuição em lote com integridade transacional atômica (migrando lançamentos
  ativos, soft-deletados e modelos recorrentes para outra categoria ativa do mesmo tipo).

Todos os endpoints exigem autenticação e isolam dados por usuario_id (defesa anti-IDOR).
"""
from datetime import date
from decimal import Decimal
from flask import Blueprint, jsonify, request
from app.models import db
from app.models.categoria import Categoria
from app.models.lancamento import Lancamento
from app.models.lancamento_recorrente import LancamentoRecorrente
from app.utils.auth import current_user, login_required, validar_posse

categorias_bp = Blueprint("categorias", __name__, url_prefix="/api/categorias")


@categorias_bp.route("", methods=["GET"])
@login_required
def listar_categorias():
    """Lista as categorias do usuário logado com cálculo de consumo orçamentário.

    Parâmetros opcionais (query params):
        tipo (str): 'receita' ou 'despesa' para filtrar.
        status (str): 'ativo' ou 'arquivado'. Padrão: todas.
        ano (int): Ano para cálculo do consumo (padrão: ano corrente).
        mes (int): Mês para cálculo do consumo (padrão: mês corrente).
    """
    usuario_id = current_user.id
    tipo_filtro = request.args.get("tipo")
    status_filtro = request.args.get("status")

    hoje = date.today()
    try:
        ano = int(request.args.get("ano", hoje.year))
        mes = int(request.args.get("mes", hoje.month))
    except (ValueError, TypeError):
        ano = hoje.year
        mes = hoje.month

    query = Categoria.query.filter_by(usuario_id=usuario_id)

    if tipo_filtro in ("receita", "despesa"):
        query = query.filter_by(tipo=tipo_filtro)

    if status_filtro in ("ativo", "arquivado"):
        query = query.filter_by(status=status_filtro)

    categorias = query.order_by(Categoria.tipo.desc(), Categoria.status.asc(), Categoria.nome.asc()).all()

    lista = [c.to_dict(ano=ano, mes=mes, incluir_consumo=True) for c in categorias]

    receitas = [c for c in lista if c["tipo"] == "receita"]
    despesas = [c for c in lista if c["tipo"] == "despesa"]

    return jsonify({
        "sucesso": True,
        "dados": {
            "categorias": lista,
            "receitas": receitas,
            "despesas": despesas,
            "periodo": {"ano": ano, "mes": mes},
        },
    }), 200


@categorias_bp.route("", methods=["POST"])
@login_required
def criar_categoria():
    """Cria uma nova categoria para o usuário autenticado.

    Corpo esperado (JSON):
        nome (str): Nome da categoria. Obrigatório (máx 100 caracteres).
        tipo (str): 'receita' ou 'despesa'. Obrigatório.
        teto_orcamento (float|null): Teto mensal opcional (apenas para despesas).
    """
    usuario_id = current_user.id
    dados = request.get_json(silent=True) or {}

    nome = (dados.get("nome") or "").strip()
    if not nome:
        return jsonify({"sucesso": False, "erro": "O nome da categoria é obrigatório."}), 400

    if len(nome) > 100:
        return jsonify({"sucesso": False, "erro": "O nome não pode exceder 100 caracteres."}), 400

    tipo = (dados.get("tipo") or "").strip().lower()
    if tipo not in ("receita", "despesa"):
        return jsonify({"sucesso": False, "erro": "O tipo da categoria deve ser 'receita' ou 'despesa'."}), 400

    teto_orcamento = None
    if tipo == "despesa" and dados.get("teto_orcamento") is not None and dados.get("teto_orcamento") != "":
        try:
            val = float(dados.get("teto_orcamento"))
            if val < 0:
                return jsonify({"sucesso": False, "erro": "O teto de orçamento não pode ser negativo."}), 400
            teto_orcamento = round(val, 2)
        except (ValueError, TypeError):
            return jsonify({"sucesso": False, "erro": "O teto de orçamento deve ser um número válido."}), 400

    # Validação de unicidade para o mesmo usuário e tipo
    existe = Categoria.query.filter_by(usuario_id=usuario_id, nome=nome, tipo=tipo).first()
    if existe:
        return jsonify({
            "sucesso": False,
            "erro": f'Você já possui uma categoria de {tipo} com o nome "{nome}".',
        }), 400

    nova_categoria = Categoria(
        usuario_id=usuario_id,
        nome=nome,
        tipo=tipo,
        teto_orcamento=teto_orcamento,
        status="ativo",
    )

    try:
        db.session.add(nova_categoria)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"sucesso": False, "erro": "Erro ao salvar a categoria. Tente novamente."}), 500

    hoje = date.today()
    return jsonify({
        "sucesso": True,
        "mensagem": f'Categoria "{nome}" criada com sucesso.',
        "dados": {"categoria": nova_categoria.to_dict(ano=hoje.year, mes=hoje.month, incluir_consumo=True)},
    }), 201


@categorias_bp.route("/<int:categoria_id>", methods=["PUT"])
@login_required
def editar_categoria(categoria_id):
    """Edita nome e teto orçamentário de uma categoria existente do usuário.

    Corpo esperado (JSON):
        nome (str): Novo nome da categoria. Obrigatório.
        teto_orcamento (float|null): Novo teto (apenas se tipo == 'despesa').
    """
    categoria = db.session.get(Categoria, categoria_id)

    if not categoria or not validar_posse(categoria, "categoria"):
        return jsonify({"sucesso": False, "erro": "Categoria não encontrada ou acesso negado."}), 404

    dados = request.get_json(silent=True) or {}
    nome = (dados.get("nome") or "").strip()

    if not nome:
        return jsonify({"sucesso": False, "erro": "O nome da categoria é obrigatório."}), 400

    if len(nome) > 100:
        return jsonify({"sucesso": False, "erro": "O nome não pode exceder 100 caracteres."}), 400

    # Verifica unicidade do novo nome (excluindo a própria categoria)
    duplicado = (
        Categoria.query
        .filter(
            Categoria.usuario_id == current_user.id,
            Categoria.nome == nome,
            Categoria.tipo == categoria.tipo,
            Categoria.id != categoria_id,
        )
        .first()
    )
    if duplicado:
        return jsonify({
            "sucesso": False,
            "erro": f'Você já possui outra categoria de {categoria.tipo} com o nome "{nome}".',
        }), 400

    # Atualiza teto apenas se for despesa
    if categoria.tipo == "despesa":
        teto_val = dados.get("teto_orcamento")
        if teto_val is None or teto_val == "":
            categoria.teto_orcamento = None
        else:
            try:
                val = float(teto_val)
                if val < 0:
                    return jsonify({"sucesso": False, "erro": "O teto de orçamento não pode ser negativo."}), 400
                categoria.teto_orcamento = round(val, 2)
            except (ValueError, TypeError):
                return jsonify({"sucesso": False, "erro": "O teto de orçamento deve ser um número válido."}), 400

    categoria.nome = nome

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"sucesso": False, "erro": "Erro ao atualizar categoria. Tente novamente."}), 500

    hoje = date.today()
    return jsonify({
        "sucesso": True,
        "mensagem": f'Categoria "{nome}" atualizada com sucesso.',
        "dados": {"categoria": categoria.to_dict(ano=hoje.year, mes=hoje.month, incluir_consumo=True)},
    }), 200


@categorias_bp.route("/<int:categoria_id>/arquivar", methods=["PATCH"])
@login_required
def arquivar_categoria(categoria_id):
    """Arquiva uma categoria ativa, impedindo novas seleções sem perder histórico."""
    categoria = db.session.get(Categoria, categoria_id)

    if not categoria or not validar_posse(categoria, "categoria"):
        return jsonify({"sucesso": False, "erro": "Categoria não encontrada ou acesso negado."}), 404

    if categoria.status == "arquivado":
        return jsonify({"sucesso": False, "erro": "Esta categoria já está arquivada."}), 400

    categoria.arquivar()

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"sucesso": False, "erro": "Erro ao arquivar categoria. Tente novamente."}), 500

    return jsonify({
        "sucesso": True,
        "mensagem": f'Categoria "{categoria.nome}" arquivada com sucesso.',
        "dados": {"categoria": categoria.to_dict()},
    }), 200


@categorias_bp.route("/<int:categoria_id>/reativar", methods=["PATCH"])
@login_required
def reativar_categoria(categoria_id):
    """Reativa uma categoria previamente arquivada."""
    categoria = db.session.get(Categoria, categoria_id)

    if not categoria or not validar_posse(categoria, "categoria"):
        return jsonify({"sucesso": False, "erro": "Categoria não encontrada ou acesso negado."}), 404

    if categoria.status == "ativo":
        return jsonify({"sucesso": False, "erro": "Esta categoria já está ativa."}), 400

    categoria.reativar()

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"sucesso": False, "erro": "Erro ao reativar categoria. Tente novamente."}), 500

    return jsonify({
        "sucesso": True,
        "mensagem": f'Categoria "{categoria.nome}" reativada com sucesso.',
        "dados": {"categoria": categoria.to_dict()},
    }), 200


@categorias_bp.route("/<int:categoria_id>", methods=["DELETE"])
@login_required
def excluir_categoria(categoria_id):
    """Exclui fisicamente uma categoria SOMENTE se não houver lançamentos ou fixos vinculados.

    Se houver histórico vinculado (ativos, soft-deletados ou recorrentes), a exclusão
    direta é bloqueada e a API orienta o arquivamento ou a reatribuição em lote.
    """
    categoria = db.session.get(Categoria, categoria_id)

    if not categoria or not validar_posse(categoria, "categoria"):
        return jsonify({"sucesso": False, "erro": "Categoria não encontrada ou acesso negado."}), 404

    # Conta lançamentos e fixos vinculados
    total_lancamentos = Lancamento.query.filter_by(categoria_id=categoria.id).count()
    total_recorrentes = LancamentoRecorrente.query.filter_by(categoria_id=categoria.id).count()

    if total_lancamentos > 0 or total_recorrentes > 0:
        return jsonify({
            "sucesso": False,
            "erro": (
                f'A categoria "{categoria.nome}" possui {total_lancamentos} lançamento(s) e '
                f'{total_recorrentes} modelo(s) fixo(s) vinculados. Não é possível excluí-la diretamente.'
            ),
            "requer_reatribuicao": True,
            "total_lancamentos": total_lancamentos,
            "total_recorrentes": total_recorrentes,
            "tipo": categoria.tipo,
        }), 400

    nome_cat = categoria.nome

    try:
        db.session.delete(categoria)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"sucesso": False, "erro": "Erro ao excluir categoria. Tente novamente."}), 500

    return jsonify({
        "sucesso": True,
        "mensagem": f'Categoria "{nome_cat}" excluída permanentemente.',
    }), 200


@categorias_bp.route("/<int:categoria_id>/reatribuir-excluir", methods=["POST"])
@login_required
def reatribuir_e_excluir(categoria_id):
    """Executa a reatribuição em lote de todo o histórico para uma nova categoria e exclui a original.

    Transação atômica que:
    1. Transfere todos os lançamentos (ativos e soft-deletados) para `nova_categoria_id`.
    2. Transfere todos os modelos recorrentes para `nova_categoria_id`.
    3. Exclui fisicamente a categoria original.

    Corpo esperado (JSON):
        nova_categoria_id (int): ID da categoria de destino (deve pertencer ao usuário e ser do mesmo tipo).
    """
    categoria_origem = db.session.get(Categoria, categoria_id)

    if not categoria_origem or not validar_posse(categoria_origem, "categoria"):
        return jsonify({"sucesso": False, "erro": "Categoria de origem não encontrada ou acesso negado."}), 404

    dados = request.get_json(silent=True) or {}
    nova_categoria_id = dados.get("nova_categoria_id")

    if not nova_categoria_id:
        return jsonify({"sucesso": False, "erro": "Informe a nova categoria para onde o histórico será transferido."}), 400

    if int(nova_categoria_id) == categoria_origem.id:
        return jsonify({"sucesso": False, "erro": "A nova categoria deve ser diferente da categoria atual."}), 400

    nova_categoria = db.session.get(Categoria, nova_categoria_id)
    if not nova_categoria or not validar_posse(nova_categoria, "categoria"):
        return jsonify({"sucesso": False, "erro": "Categoria de destino não encontrada ou acesso negado."}), 404

    if nova_categoria.tipo != categoria_origem.tipo:
        return jsonify({
            "sucesso": False,
            "erro": f'A categoria de destino deve ser do mesmo tipo ({categoria_origem.tipo}).',
        }), 400

    if nova_categoria.status != "ativo":
        return jsonify({
            "sucesso": False,
            "erro": "A categoria de destino deve estar ativa para receber o histórico.",
        }), 400

    nome_origem = categoria_origem.nome
    nome_destino = nova_categoria.nome

    try:
        # Transação atômica de migração
        total_lancamentos = (
            Lancamento.query
            .filter_by(categoria_id=categoria_origem.id)
            .update({Lancamento.categoria_id: nova_categoria.id}, synchronize_session=False)
        )

        total_recorrentes = (
            LancamentoRecorrente.query
            .filter_by(categoria_id=categoria_origem.id)
            .update({LancamentoRecorrente.categoria_id: nova_categoria.id}, synchronize_session=False)
        )

        db.session.delete(categoria_origem)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({
            "sucesso": False,
            "erro": "Falha na transação de reatribuição. As alterações foram revertidas. Tente novamente.",
        }), 500

    return jsonify({
        "sucesso": True,
        "mensagem": (
            f'{total_lancamentos} lançamento(s) e {total_recorrentes} fixo(s) foram '
            f'transferidos de "{nome_origem}" para "{nome_destino}", e a categoria "{nome_origem}" '
            "foi excluída com sucesso."
        ),
        "dados": {
            "total_lancamentos_transferidos": total_lancamentos,
            "total_recorrentes_transferidos": total_recorrentes,
            "categoria_destino_id": nova_categoria.id,
        },
    }), 200
